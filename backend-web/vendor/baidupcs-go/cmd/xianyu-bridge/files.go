package main

import (
	"crypto/md5"
	"crypto/tls"
	"encoding/hex"
	"encoding/json"
	"hash/crc32"
	"io"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"time"

	"github.com/qjfoidnh/BaiduPCS-Go/baidupcs"
	"github.com/qjfoidnh/BaiduPCS-Go/requester/multipartreader"
)

const maxTransferBytes = int64(4 << 30)
const uploadBlockBytes = int64(4 << 20)

// Python supplies files in project-private staging. Reject symlinks and use
// os.Root so a file replacement cannot escape the already opened directory.
// Optional BAIDUPCS_BRIDGE_FILE_ROOT narrows this further to the caller's data dir.
func localRoot(name string, uploading bool) (*os.Root, string, error) {
	if !filepath.IsAbs(name) || hasControl(name) || len(name) > 4096 {
		return nil, "", fail("invalid_input", "input", "本地文件路径无效")
	}
	name = filepath.Clean(name)
	dir := filepath.Dir(name)
	base := filepath.Base(name)
	if base == "." || base == string(filepath.Separator) {
		return nil, "", fail("invalid_input", "input", "本地文件路径无效")
	}
	real, e := filepath.EvalSymlinks(dir)
	if e != nil || real != dir {
		return nil, "", fail("invalid_input", "input", "不能使用链接目录作为传输路径")
	}
	if boundary := os.Getenv("BAIDUPCS_BRIDGE_FILE_ROOT"); boundary != "" {
		boundary = filepath.Clean(boundary)
		rel, e := filepath.Rel(boundary, name)
		if !filepath.IsAbs(boundary) || e != nil || rel == ".." || strings.HasPrefix(rel, ".."+string(filepath.Separator)) {
			return nil, "", fail("invalid_input", "input", "文件不在允许的传输目录内")
		}
	}
	if info, e := os.Lstat(name); e == nil {
		if !uploading || !info.Mode().IsRegular() {
			return nil, "", fail("local_file_exists", "input", "下载目标已存在或上传来源不是普通文件")
		}
	} else if uploading || !os.IsNotExist(e) {
		return nil, "", fail("invalid_input", "input", "无法读取本地传输文件")
	}
	root, e := os.OpenRoot(dir)
	if e != nil {
		return nil, "", fail("invalid_input", "input", "无法打开本地传输目录")
	}
	return root, base, nil
}

type sectionWithLen struct {
	*io.SectionReader
	size int64
}

func (r *sectionWithLen) Len() int64 { return r.size }

type fileHashes struct {
	full, slice, crc string
	blocks           []string
}

func hashFile(f *os.File, size int64) (fileHashes, error) {
	full := md5.New()
	crc := crc32.NewIEEE()
	slice := md5.New()
	blocks := []string{}
	if size == 0 {
		blocks = append(blocks, baidupcs.EmptyContentMD5)
	}
	for offset := int64(0); offset < size; offset += uploadBlockBytes {
		n := uploadBlockBytes
		if size-offset < n {
			n = size - offset
		}
		block := md5.New()
		r := io.NewSectionReader(f, offset, n)
		if copied, e := io.Copy(io.MultiWriter(full, crc, block), r); e != nil || copied != n {
			return fileHashes{}, fail("local_read_failed", "input", "本地上传文件读取失败")
		}
		blocks = append(blocks, hex.EncodeToString(block.Sum(nil)))
	}
	sliceSize := int64(256 << 10)
	if size < sliceSize {
		sliceSize = size
	}
	if _, e := io.Copy(slice, io.NewSectionReader(f, 0, sliceSize)); e != nil {
		return fileHashes{}, fail("local_read_failed", "input", "本地上传文件读取失败")
	}
	return fileHashes{full: hex.EncodeToString(full.Sum(nil)), slice: hex.EncodeToString(slice.Sum(nil)), crc: strconv.FormatUint(uint64(crc.Sum32()), 10), blocks: blocks}, nil
}

func uploadFile(pcs *baidupcs.BaiduPCS, args json.RawMessage) (any, error) {
	var a struct {
		Local string `json:"local_path"`
		Path  string `json:"path"`
	}
	if e := decodeArgs(args, &a); e != nil {
		return nil, e
	}
	target, e := cleanRemote(a.Path, false)
	if e != nil {
		return nil, e
	}
	root, base, e := localRoot(a.Local, true)
	if e != nil {
		return nil, e
	}
	defer root.Close()
	f, e := root.Open(base)
	if e != nil {
		return nil, fail("local_read_failed", "input", "无法打开本地上传文件")
	}
	defer f.Close()
	info, e := f.Stat()
	if e != nil || !info.Mode().IsRegular() || info.Size() > maxTransferBytes {
		return nil, fail("file_too_large", "input", "上传文件不是普通文件或超过 4 GiB 限制")
	}
	if e = ensureAbsent(pcs, target); e != nil {
		return nil, e
	}
	hashes, e := hashFile(f, info.Size())
	if e != nil {
		return nil, e
	}
	client := pcs.GetClient()
	oldTimeout := client.Timeout
	client.SetTimeout(5 * time.Minute)
	defer client.SetTimeout(oldTimeout)
	body, pe := pcs.PrepareUploadPrecreate(target, hashes.full, hashes.slice, hashes.crc, info.Size(), hashes.blocks)
	pre, e := readProvider(body, pe)
	if e != nil {
		return nil, e
	}
	if p, ok := pre["info"].(map[string]any); ok && stringValue(p["path"]) != "" && stringValue(p["path"]) != target {
		return nil, fail("upload_path_mismatch", "provider", "百度网盘上传结果路径发生变化")
	}
	if intValue(pre["return_type"]) != 2 {
		uploadID := stringValue(pre["uploadid"])
		if uploadID == "" {
			return nil, fail("invalid_provider_response", "provider", "百度网盘未返回有效上传任务")
		}
		for part, wanted := range hashes.blocks {
			offset := int64(part) * uploadBlockBytes
			n := uploadBlockBytes
			if info.Size()-offset < n {
				n = info.Size() - offset
			}
			actual, pe := pcs.UploadTmpFile(uploadID, target, part, offset, func(uploadURL string, jar http.CookieJar) (*http.Response, error) {
				u, e := url.Parse(uploadURL)
				if e != nil || !safeURL(u) {
					return nil, fail("unsafe_provider_url", "provider", "百度网盘返回了不安全的上传地址")
				}
				mr := multipartreader.NewMultipartReader()
				mr.AddFormFile("uploadedfile", "", &sectionWithLen{SectionReader: io.NewSectionReader(f, offset, n), size: n})
				if e := mr.CloseMultipart(); e != nil {
					return nil, fail("bridge_failure", "provider", "无法准备上传文件")
				}
				return client.Req(http.MethodPost, uploadURL, mr, map[string]string{"User-Agent": baidupcs.NetdiskUA})
			})
			if pe != nil {
				return nil, providerFailure(pe)
			}
			if !strings.EqualFold(baidupcs.DecryptMD5(actual), wanted) {
				return nil, fail("upload_checksum_mismatch", "provider", "百度网盘上传分片校验失败")
			}
		}
		// Use the mature library's prepare method with rtype=0. The public helper
		// falls back to rtype=3 (overwrite) for unrecognised policies.
		body, pe = pcs.PrepareUploadCreateSuperFile(uploadID, "0", info.Size(), target, hashes.blocks)
		created, e := readProvider(body, pe)
		if e != nil {
			return nil, e
		}
		if p := stringValue(created["path"]); p != "" && p != target {
			return nil, fail("upload_path_mismatch", "provider", "百度网盘上传结果路径发生变化")
		}
	}
	files, e := metadata(pcs, []string{target})
	if e != nil {
		return nil, e
	}
	if len(files) != 1 || files[0].Path != target || files[0].Isdir || files[0].Size != info.Size() || (files[0].MD5 != "" && !strings.EqualFold(files[0].MD5, hashes.full)) {
		return nil, fail("upload_verification_failed", "provider", "百度网盘上传结果校验失败")
	}
	return map[string]any{"status": "success", "files": files}, nil
}

func downloadFile(pcs *baidupcs.BaiduPCS, args json.RawMessage) (any, error) {
	var a struct {
		Path  string `json:"path"`
		Local string `json:"local_path"`
		Max   int64  `json:"max_bytes"`
	}
	if e := decodeArgs(args, &a); e != nil {
		return nil, e
	}
	p, e := cleanRemote(a.Path, false)
	if e != nil {
		return nil, e
	}
	if a.Max <= 0 || a.Max > maxTransferBytes {
		return nil, fail("invalid_input", "input", "下载必须指定不超过 4 GiB 的大小限制")
	}
	files, e := metadata(pcs, []string{p})
	if e != nil {
		return nil, e
	}
	if len(files) != 1 || files[0].Isdir {
		return nil, fail("invalid_input", "input", "只能下载单个普通文件")
	}
	if files[0].Size < 0 || files[0].Size > a.Max {
		return nil, fail("file_too_large", "input", "网盘文件超过下载大小限制")
	}
	root, base, e := localRoot(a.Local, false)
	if e != nil {
		return nil, e
	}
	defer root.Close()
	f, e := root.OpenFile(base, os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0600)
	if e != nil {
		return nil, fail("local_file_exists", "input", "下载目标无法创建或已存在")
	}
	completed := false
	defer func() {
		f.Close()
		if !completed {
			_ = root.Remove(base)
		}
	}()
	var link string
	located, pe := pcs.LocateDownload(p)
	if pe == nil && located != nil {
		if u := located.SingleURL(true); u != nil {
			link = u.String()
		}
	}
	if link == "" {
		id, e := strconv.ParseInt(files[0].FsID, 10, 64)
		if e != nil {
			return nil, fail("invalid_provider_response", "provider", "百度网盘文件标识无效")
		}
		links, pe := pcs.LocatePanAPIDownload(id)
		if pe != nil {
			return nil, providerFailure(pe)
		}
		if len(links) > 0 && links[0] != nil {
			link = links[0].Dlink
		}
	}
	u, e := url.Parse(link)
	if e != nil || !safeURL(u) {
		return nil, fail("invalid_download_url", "provider", "百度网盘未返回有效下载地址")
	}
	client := &http.Client{Timeout: 10 * time.Minute, Jar: pcs.GetClient().Jar, CheckRedirect: safeRedirect, Transport: &safeTransport{next: &http.Transport{Proxy: http.ProxyFromEnvironment, TLSClientConfig: &tls.Config{MinVersion: tls.VersionTLS12}, TLSHandshakeTimeout: 20 * time.Second, ResponseHeaderTimeout: 45 * time.Second}, maxBody: 0}}
	defer client.CloseIdleConnections()
	req, e := http.NewRequest(http.MethodGet, u.String(), nil)
	if e != nil {
		return nil, fail("invalid_download_url", "provider", "百度网盘未返回有效下载地址")
	}
	req.Header.Set("User-Agent", baidupcs.NetdiskUA)
	resp, e := client.Do(req)
	if e != nil {
		return nil, providerFailure(e)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return nil, fail("download_failed", "provider", "百度网盘文件下载失败")
	}
	if resp.ContentLength > a.Max {
		return nil, fail("file_too_large", "input", "网盘文件超过下载大小限制")
	}
	hash := md5.New()
	written, e := io.Copy(io.MultiWriter(f, hash), io.LimitReader(resp.Body, a.Max+1))
	if e != nil {
		return nil, providerFailure(e)
	}
	if written > a.Max || written != files[0].Size || (files[0].MD5 != "" && !strings.EqualFold(hex.EncodeToString(hash.Sum(nil)), files[0].MD5)) {
		return nil, fail("download_verification_failed", "provider", "百度网盘下载文件大小或校验值不一致")
	}
	if f.Sync() != nil || f.Close() != nil {
		return nil, fail("local_write_failed", "input", "无法保存下载文件")
	}
	completed = true
	return map[string]any{"status": "success", "size": written}, nil
}
