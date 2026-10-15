// Xianyu's private, single-request adapter to BaiduPCS-Go. Credentials are read
// from stdin only. This executable deliberately does not import pcsconfig.
package main

import (
	"bytes"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/url"
	"os"
	"path"
	"strconv"
	"strings"
	"time"
	"unicode"

	"github.com/qjfoidnh/BaiduPCS-Go/baidupcs"
	"github.com/qjfoidnh/BaiduPCS-Go/baidupcs/pcserror"
	"github.com/qjfoidnh/BaiduPCS-Go/pcsverbose"
)

const (
	maxInputBytes  = 1 << 20
	maxJSONBytes   = 8 << 20
	maxPaths       = 100
	maxSearchItems = 10000
	// This is the maintained upstream CLI's default PCS application ID.
	// Web-pan endpoints independently retain baidupcs.PanAppID (250528).
	pcsAppID = 266719
)

type credentials struct {
	BDUSS   string          `json:"bduss"`
	SToken  string          `json:"stoken"`
	Cookies string          `json:"cookies"`
	UID     json.RawMessage `json:"uid"`
}

type request struct {
	Operation   string          `json:"operation"`
	Credentials credentials     `json:"credentials"`
	Args        json.RawMessage `json:"args"`
}

type failure struct {
	Code         string
	Kind         string
	Message      string
	ProviderCode int
}

func (e *failure) Error() string { return e.Message }

type response struct {
	Success      bool   `json:"success"`
	Data         any    `json:"data,omitempty"`
	ErrorCode    string `json:"error_code,omitempty"`
	Kind         string `json:"kind,omitempty"`
	Message      string `json:"message,omitempty"`
	ProviderCode int    `json:"provider_code,omitempty"`
}

func fail(code, kind, message string) *failure {
	return &failure{Code: code, Kind: kind, Message: message}
}

func providerFailure(err error) *failure {
	var safe *failure
	if errors.As(err, &safe) {
		return safe
	}
	if pe, ok := err.(pcserror.Error); ok {
		code := pe.GetRemoteErrCode()
		if pe.GetErrType() == pcserror.ErrTypeNetError {
			return fail("network_error", "network", "无法连接百度网盘，请稍后重试")
		}
		f := remoteFailure(code)
		if code == 0 {
			f.Code = "invalid_provider_response"
			f.Message = "百度网盘返回的数据无法解析"
		}
		return f
	}
	// Do not include arbitrary library/network error text: it can contain URLs,
	// account cookies, provider response bodies or local filesystem paths.
	return fail("network_error", "network", "百度网盘请求未完成，请稍后重试")
}

func remoteFailure(code int) *failure {
	f := fail("provider_error", "provider", "百度网盘拒绝了本次操作")
	f.ProviderCode = code
	switch code {
	case -6, 111, 112, 31023, 31024, 31045:
		f.Code, f.Kind, f.Message = "authorization_required", "auth", "百度网盘登录已失效，请重新扫码登录"
	case -9, 31066:
		f.Code, f.Message = "not_found", "百度网盘文件或目录不存在"
	case -8, 31061, 31062:
		f.Code, f.Message = "already_exists", "百度网盘目标文件或目录已存在"
	}
	return f
}

func envelope(data any, err error) response {
	if err == nil {
		return response{Success: true, Data: data}
	}
	f := providerFailure(err)
	return response{ErrorCode: f.Code, Kind: f.Kind, Message: f.Message, ProviderCode: f.ProviderCode}
}

func main() {
	// Preserve the protocol writer before sending all upstream console output to
	// stderr. Python discards that stream; debug mode cannot be enabled by env.
	out := os.Stdout
	os.Stdout = os.Stderr
	pcsverbose.IsVerbose = false
	pcsverbose.Outputs = []io.Writer{io.Discard}
	result := response{}
	defer func() {
		if recover() != nil {
			result = envelope(nil, fail("bridge_failure", "provider", "百度网盘操作未完成"))
		}
		body, err := json.Marshal(result)
		if err != nil || len(body) > maxJSONBytes {
			body, _ = json.Marshal(envelope(nil, fail("response_too_large", "provider", "返回结果超过接口限制，请缩小查询范围")))
		}
		_, _ = out.Write(append(body, '\n'))
	}()
	req, err := readRequest(os.Stdin)
	if err != nil {
		result = envelope(nil, err)
		return
	}
	pcs, err := newPCS(req.Credentials)
	if err != nil {
		result = envelope(nil, err)
		return
	}
	data, err := dispatch(pcs, req)
	result = envelope(data, err)
}

func readRequest(input io.Reader) (*request, error) {
	body, err := io.ReadAll(io.LimitReader(input, maxInputBytes+1))
	if err != nil || len(body) > maxInputBytes {
		return nil, fail("invalid_input", "input", "请求大小超过限制")
	}
	var r request
	d := json.NewDecoder(bytes.NewReader(body))
	d.DisallowUnknownFields()
	if d.Decode(&r) != nil || r.Operation == "" {
		return nil, fail("invalid_input", "input", "请求格式错误")
	}
	var trailing any
	if d.Decode(&trailing) != io.EOF {
		return nil, fail("invalid_input", "input", "仅允许一个请求")
	}
	if len(r.Args) == 0 || string(r.Args) == "null" {
		r.Args = json.RawMessage(`{}`)
	}
	return &r, nil
}

func newPCS(c credentials) (*baidupcs.BaiduPCS, error) {
	if len(c.BDUSS)+len(c.SToken)+len(c.Cookies) > 65536 || hasControl(c.BDUSS+c.SToken+c.Cookies) {
		return nil, fail("invalid_credentials", "auth", "百度网盘登录凭证格式错误")
	}
	// Parse the header with net/http first, instead of passing malformed segments
	// to the upstream parser, which prints those segments to stdout.
	h := &http.Request{Header: http.Header{"Cookie": []string{c.Cookies}}}
	cookieMap := map[string]*http.Cookie{}
	for _, cookie := range h.Cookies() {
		cookieMap[cookie.Name] = cookie
	}
	if c.BDUSS != "" {
		cookieMap["BDUSS"] = &http.Cookie{Name: "BDUSS", Value: c.BDUSS}
	}
	if c.SToken != "" {
		cookieMap["STOKEN"] = &http.Cookie{Name: "STOKEN", Value: c.SToken}
	}
	if cookieMap["BDUSS"] == nil || cookieMap["BDUSS"].Value == "" {
		return nil, fail("credentials_missing", "auth", "请先扫码登录百度网盘")
	}
	parts := make([]string, 0, len(cookieMap))
	for _, c := range cookieMap {
		if c.Valid() != nil {
			return nil, fail("invalid_credentials", "auth", "百度网盘登录凭证格式错误")
		}
		parts = append(parts, c.Name+"="+c.Value)
	}
	pcs := baidupcs.NewPCSWithCookieStr(pcsAppID, strings.Join(parts, "; "))
	pcs.SetHTTPS(true)
	pcs.SetPCSUserAgent(baidupcs.NetdiskUA)
	pcs.SetPanUserAgent(baidupcs.NetdiskUA)
	client := pcs.GetClient()
	client.SetHTTPSecure(true)
	client.SetTimeout(45 * time.Second)
	client.CheckRedirect = safeRedirect
	client.Transport = &safeTransport{next: client.Transport, maxBody: maxJSONBytes}
	uid := strings.Trim(string(c.UID), "\"")
	if uid != "" && uid != "null" {
		value, e := strconv.ParseUint(uid, 10, 64)
		if e != nil {
			return nil, fail("invalid_input", "input", "百度网盘账号标识格式错误")
		}
		pcs.SetUID(value)
	}
	return pcs, nil
}

func hasControl(s string) bool { return strings.IndexFunc(s, unicode.IsControl) >= 0 }

func safeURL(u *url.URL) bool {
	if u == nil || u.Scheme != "https" || u.User != nil || (u.Port() != "" && u.Port() != "443") {
		return false
	}
	h := strings.ToLower(u.Hostname())
	return h == "baidu.com" || strings.HasSuffix(h, ".baidu.com") || h == "baidupcs.com" || strings.HasSuffix(h, ".baidupcs.com")
}

func safeRedirect(req *http.Request, via []*http.Request) error {
	if len(via) >= 5 || !safeURL(req.URL) {
		return fail("unsafe_provider_url", "provider", "百度网盘返回了不安全的跳转地址")
	}
	return nil
}

type safeTransport struct {
	next    http.RoundTripper
	maxBody int64
}

func (t *safeTransport) RoundTrip(r *http.Request) (*http.Response, error) {
	if !safeURL(r.URL) {
		return nil, fail("unsafe_provider_url", "provider", "百度网盘返回了不安全的请求地址")
	}
	resp, err := t.next.RoundTrip(r)
	if err != nil {
		return nil, err
	}
	if t.maxBody > 0 {
		resp.Body = &boundedBody{ReadCloser: resp.Body, left: t.maxBody}
	}
	return resp, nil
}

type boundedBody struct {
	io.ReadCloser
	left int64
}

func (b *boundedBody) Read(p []byte) (int, error) {
	if b.left <= 0 {
		var probe [1]byte
		n, e := b.ReadCloser.Read(probe[:])
		if n != 0 {
			return 0, fail("response_too_large", "provider", "百度网盘返回的数据超过接口限制")
		}
		return 0, e
	}
	if int64(len(p)) > b.left {
		p = p[:b.left]
	}
	n, e := b.ReadCloser.Read(p)
	b.left -= int64(n)
	return n, e
}

func decodeArgs(raw json.RawMessage, target any) error {
	if json.Unmarshal(raw, target) != nil {
		return fail("invalid_input", "input", "操作参数格式错误")
	}
	return nil
}

func cleanRemote(value string, allowRoot bool) (string, error) {
	if len(value) == 0 || len(value) > 4096 || !strings.HasPrefix(value, "/") || hasControl(value) || strings.Contains(value, "\\") {
		return "", fail("invalid_input", "input", "网盘路径必须是绝对路径")
	}
	for _, part := range strings.Split(value, "/") {
		if part == ".." || part == "." {
			return "", fail("invalid_input", "input", "网盘路径不能包含相对路径")
		}
	}
	value = path.Clean(value)
	if !allowRoot && value == "/" {
		return "", fail("invalid_input", "input", "不能对网盘根目录执行该操作")
	}
	return value, nil
}

func cleanPaths(paths []string) ([]string, error) {
	if len(paths) == 0 || len(paths) > maxPaths {
		return nil, fail("invalid_input", "input", "请选择 1 至 100 个文件或目录")
	}
	res := make([]string, 0, len(paths))
	seen := map[string]bool{}
	for _, p := range paths {
		p, e := cleanRemote(p, false)
		if e != nil {
			return nil, e
		}
		if !seen[p] {
			res = append(res, p)
			seen[p] = true
		}
	}
	return res, nil
}

func pageArgs(page, size int) (int, int, error) {
	if page == 0 {
		page = 1
	}
	if size == 0 {
		size = 100
	}
	if page < 1 || page > 100000 || size < 1 || size > 1000 {
		return 0, 0, fail("invalid_input", "input", "分页参数超出范围")
	}
	return page, size, nil
}

type fileInfo struct {
	FsID     string `json:"fs_id"`
	Path     string `json:"path"`
	Filename string `json:"filename"`
	Size     int64  `json:"size"`
	Isdir    bool   `json:"isdir"`
	Mtime    int64  `json:"server_mtime"`
	MD5      string `json:"md5,omitempty"`
}

func fileFromLibrary(f *baidupcs.FileDirectory) fileInfo {
	return fileInfo{FsID: strconv.FormatInt(f.FsID, 10), Path: f.Path, Filename: f.Filename, Size: f.Size, Isdir: f.Isdir, Mtime: f.Mtime, MD5: f.MD5}
}

func stringValue(v any) string {
	switch x := v.(type) {
	case string:
		return x
	case json.Number:
		return string(x)
	}
	return ""
}
func intValue(v any) int64 { n, _ := strconv.ParseInt(stringValue(v), 10, 64); return n }
func fileFromRaw(raw map[string]any) fileInfo {
	f := fileInfo{FsID: stringValue(raw["fs_id"]), Path: stringValue(raw["path"]), Filename: stringValue(raw["server_filename"]), Size: intValue(raw["size"]), Isdir: intValue(raw["isdir"]) == 1, Mtime: intValue(raw["server_mtime"]), MD5: stringValue(raw["md5"])}
	if f.Filename == "" {
		f.Filename = path.Base(f.Path)
	}
	if f.Mtime == 0 {
		f.Mtime = intValue(raw["mtime"])
	}
	if f.MD5 != "" {
		f.MD5 = baidupcs.DecryptMD5(f.MD5)
	}
	return f
}

type resourceKind uint8

const (
	resourceNone resourceKind = iota
	resourceMeta
	resourceMkdir
	resourceCopyMove
	resourceDelete
)

func readProvider(body io.ReadCloser, err error) (map[string]any, error) {
	return readProviderResource(body, err, resourceNone, nil)
}

// PCS mkdir and metadata successful responses omit an API status code. Only
// these verified resource shapes may establish success without such a code.
func readProviderResource(body io.ReadCloser, err error, kind resourceKind, expected []string) (map[string]any, error) {
	if err != nil {
		if body != nil {
			body.Close()
		}
		return nil, providerFailure(err)
	}
	if body == nil {
		return nil, fail("invalid_provider_response", "provider", "百度网盘未返回有效结果")
	}
	defer body.Close()
	b, e := io.ReadAll(io.LimitReader(body, maxJSONBytes+1))
	if e != nil || len(b) > maxJSONBytes {
		return nil, fail("invalid_provider_response", "provider", "百度网盘返回的数据无法解析")
	}
	var payload map[string]any
	d := json.NewDecoder(bytes.NewReader(b))
	d.UseNumber()
	if d.Decode(&payload) != nil || payload == nil {
		return nil, fail("invalid_provider_response", "provider", "百度网盘返回的数据无法解析")
	}
	var trailing any
	if d.Decode(&trailing) != io.EOF {
		return nil, fail("invalid_provider_response", "provider", "百度网盘返回的数据无法解析")
	}
	statusFound := false
	for _, key := range []string{"errno", "error_code", "err_no"} {
		if v, ok := payload[key]; ok {
			statusFound = true
			code, e := strconv.ParseInt(stringValue(v), 10, 64)
			if e != nil {
				return nil, fail("invalid_provider_response", "provider", "百度网盘返回的数据无法解析")
			}
			if code != 0 {
				return nil, remoteFailure(int(code))
			}
		}
	}
	if (!statusFound || kind == resourceCopyMove || kind == resourceDelete) && !validResourcePayload(payload, kind, expected) {
		return nil, fail("invalid_provider_response", "provider", "百度网盘未返回有效操作状态")
	}
	return payload, nil
}

func validResourcePayload(payload map[string]any, kind resourceKind, expected []string) bool {
	validEntry := func(row map[string]any, want string, directory bool) bool {
		if intValue(row["fs_id"]) <= 0 || stringValue(row["path"]) != want {
			return false
		}
		dir, e := strconv.ParseInt(stringValue(row["isdir"]), 10, 64)
		if e != nil || (dir != 0 && dir != 1) || (directory && dir != 1) {
			return false
		}
		if s, ok := row["status"]; ok {
			n, e := strconv.ParseInt(stringValue(s), 10, 64)
			if e != nil || n != 0 {
				return false
			}
		}
		for _, key := range []string{"errno", "error_code", "err_no"} {
			if s, ok := row[key]; ok {
				n, e := strconv.ParseInt(stringValue(s), 10, 64)
				if e != nil || n != 0 {
					return false
				}
			}
		}
		return true
	}
	switch kind {
	case resourceCopyMove:
		if len(expected) != 2 || intValue(payload["request_id"]) <= 0 {
			return false
		}
		for key := range payload {
			if key != "extra" && key != "request_id" && key != "errno" && key != "error_code" && key != "err_no" {
				return false
			}
		}
		extra, ok := payload["extra"].(map[string]any)
		if !ok || len(extra) != 1 {
			return false
		}
		rows, ok := extra["list"].([]any)
		if !ok || len(rows) != 1 {
			return false
		}
		row, ok := rows[0].(map[string]any)
		if !ok || stringValue(row["from"]) != expected[0] || stringValue(row["to"]) != expected[1] {
			return false
		}
		for key := range row {
			if key != "from" && key != "to" && key != "from_fs_id" && key != "to_fs_id" {
				return false
			}
		}
		for _, key := range []string{"from_fs_id", "to_fs_id"} {
			if v, ok := row[key]; ok && intValue(v) <= 0 {
				return false
			}
		}
		return true
	case resourceDelete:
		if len(expected) == 0 || intValue(payload["request_id"]) <= 0 {
			return false
		}
		for key := range payload {
			if key != "request_id" && key != "errno" && key != "error_code" && key != "err_no" {
				return false
			}
		}
		return true
	case resourceMkdir:
		return len(expected) == 1 && validEntry(payload, expected[0], true)
	case resourceMeta:
		rows, ok := payload["list"].([]any)
		if !ok || len(rows) != len(expected) || len(expected) == 0 {
			return false
		}
		wanted := map[string]bool{}
		for _, p := range expected {
			wanted[p] = true
		}
		seen := map[string]bool{}
		for _, entry := range rows {
			row, ok := entry.(map[string]any)
			if !ok {
				return false
			}
			p := stringValue(row["path"])
			if !wanted[p] || seen[p] || !validEntry(row, p, false) {
				return false
			}
			seen[p] = true
		}
		return len(seen) == len(wanted)
	}
	return false
}

func listPage(pcs *baidupcs.BaiduPCS, dir string, page, size int) (map[string]any, error) {
	body, pe := pcs.PrepareFilesDirectoriesList(dir, nil, size, page)
	raw, e := readProvider(body, pe)
	if e != nil {
		return nil, e
	}
	rows, ok := raw["list"].([]any)
	if !ok {
		return nil, fail("invalid_provider_response", "provider", "百度网盘未返回有效文件列表")
	}
	files := make([]fileInfo, 0, len(rows))
	for _, row := range rows {
		v, ok := row.(map[string]any)
		if !ok {
			return nil, fail("invalid_provider_response", "provider", "百度网盘文件信息格式错误")
		}
		files = append(files, fileFromRaw(v))
	}
	return map[string]any{"files": files, "has_more": len(files) >= size}, nil
}

func metadata(pcs *baidupcs.BaiduPCS, paths []string) ([]fileInfo, error) {
	body, pe := pcs.PrepareFilesDirectoriesBatchMeta(paths...)
	raw, e := readProviderResource(body, pe, resourceMeta, paths)
	if e != nil {
		return nil, e
	}
	if !validResourcePayload(raw, resourceMeta, paths) {
		return nil, fail("invalid_provider_response", "provider", "百度网盘文件信息与请求路径不一致")
	}
	rows, ok := raw["list"].([]any)
	if !ok {
		return nil, fail("invalid_provider_response", "provider", "百度网盘文件信息格式错误")
	}
	if len(rows) == 0 {
		return nil, remoteFailure(31066)
	}
	files := make([]fileInfo, 0, len(rows))
	for _, row := range rows {
		f, ok := row.(map[string]any)
		if !ok || intValue(f["fs_id"]) <= 0 || stringValue(f["path"]) == "" {
			return nil, fail("invalid_provider_response", "provider", "百度网盘文件信息格式错误")
		}
		files = append(files, fileFromRaw(f))
	}
	return files, nil
}

func ensureAbsent(pcs *baidupcs.BaiduPCS, p string) error {
	_, e := metadata(pcs, []string{p})
	if e == nil {
		return fail("already_exists", "provider", "百度网盘目标文件或目录已存在，不能覆盖")
	}
	var f *failure
	if errors.As(e, &f) && f.Code == "not_found" {
		return nil
	}
	return e
}

func requireGone(pcs *baidupcs.BaiduPCS, p, operation string) error {
	_, e := metadata(pcs, []string{p})
	var f *failure
	if errors.As(e, &f) && f.Code == "not_found" {
		return nil
	}
	if e != nil {
		return e
	}
	return fail(operation+"_verification_failed", "provider", "百度网盘操作后源路径仍然存在，请刷新核验")
}

func sameResource(before, after fileInfo) bool {
	return before.Size == after.Size && before.Isdir == after.Isdir && (before.MD5 == "" || strings.EqualFold(before.MD5, after.MD5))
}

func dispatch(pcs *baidupcs.BaiduPCS, r *request) (any, error) {
	switch r.Operation {
	case "profile":
		body, pe := pcs.PrepareUK()
		raw, e := readProvider(body, pe)
		if e != nil {
			return nil, e
		}
		records, ok := raw["records"].([]any)
		if !ok || len(records) == 0 {
			return nil, fail("authorization_required", "auth", "无法验证百度网盘账号，请重新扫码登录")
		}
		record, ok := records[0].(map[string]any)
		if !ok || intValue(record["uk"]) <= 0 {
			return nil, fail("authorization_required", "auth", "无法验证百度网盘账号，请重新扫码登录")
		}
		uid := intValue(record["uk"])
		pcs.SetUID(uint64(uid))
		if _, e = listPage(pcs, "/", 1, 1); e != nil {
			return nil, e
		}
		res := map[string]any{"uid": strconv.FormatInt(uid, 10)}
		for _, key := range []string{"display_name", "uname", "username", "user_name", "name"} {
			if name := stringValue(record[key]); name != "" {
				res["nickname"] = name
				break
			}
		}
		quota, used, qe := pcs.QuotaInfo()
		if qe == nil {
			res["capacity_bytes"] = quota
			res["used_bytes"] = used
		}
		return res, nil
	case "list":
		var a struct {
			Dir  string `json:"dir"`
			Path string `json:"path"`
			Page int    `json:"page"`
			Size int    `json:"page_size"`
		}
		if e := decodeArgs(r.Args, &a); e != nil {
			return nil, e
		}
		if a.Dir == "" {
			a.Dir = a.Path
		}
		if a.Dir == "" {
			a.Dir = "/"
		}
		dir, e := cleanRemote(a.Dir, true)
		if e != nil {
			return nil, e
		}
		page, size, e := pageArgs(a.Page, a.Size)
		if e != nil {
			return nil, e
		}
		return listPage(pcs, dir, page, size)
	case "search":
		var a struct {
			Dir       string `json:"dir"`
			Keyword   string `json:"keyword"`
			Page      int    `json:"page"`
			Size      int    `json:"page_size"`
			Max       int    `json:"max_items"`
			Recursive *bool  `json:"recursive"`
		}
		if e := decodeArgs(r.Args, &a); e != nil {
			return nil, e
		}
		if a.Dir == "" {
			a.Dir = "/"
		}
		dir, e := cleanRemote(a.Dir, true)
		if e != nil {
			return nil, e
		}
		if len(strings.TrimSpace(a.Keyword)) == 0 || len(a.Keyword) > 256 || hasControl(a.Keyword) {
			return nil, fail("invalid_input", "input", "请输入有效搜索关键词")
		}
		page, size := a.Page, a.Size
		if page == 0 {
			page = 1
		}
		if size == 0 {
			size = 100
		}
		if page < 1 || page > 100000 || size < 1 || size > maxSearchItems {
			return nil, fail("invalid_input", "input", "搜索分页参数超出范围")
		}
		if a.Max == 0 {
			a.Max = maxSearchItems
		}
		if a.Max < 1 || a.Max > maxSearchItems {
			return nil, fail("invalid_input", "input", "搜索条目上限超出范围")
		}
		recursive := true
		if a.Recursive != nil {
			recursive = *a.Recursive
		}
		rows, pe := pcs.Search(dir, a.Keyword, recursive)
		if pe != nil {
			return nil, providerFailure(pe)
		}
		if len(rows) >= a.Max {
			return nil, fail("search_limit_exceeded", "input", "搜索结果达到接口上限，请缩小目录或关键词范围")
		}
		start := (page - 1) * size
		if start > len(rows) {
			start = len(rows)
		}
		end := start + size
		if end > len(rows) {
			end = len(rows)
		}
		files := make([]fileInfo, 0, end-start)
		for _, f := range rows[start:end] {
			if f != nil {
				files = append(files, fileFromLibrary(f))
			}
		}
		return map[string]any{"files": files, "has_more": end < len(rows), "total": len(rows)}, nil
	case "meta", "delete":
		var a struct {
			Paths []string `json:"paths"`
		}
		if e := decodeArgs(r.Args, &a); e != nil {
			return nil, e
		}
		paths, e := cleanPaths(a.Paths)
		if e != nil {
			return nil, e
		}
		if r.Operation == "meta" {
			files, e := metadata(pcs, paths)
			return map[string]any{"files": files}, e
		}
		before, e := metadata(pcs, paths)
		if e != nil {
			return nil, e
		}
		if len(before) != len(paths) {
			return nil, fail("invalid_provider_response", "provider", "百度网盘删除前文件信息不完整")
		}
		body, pe := pcs.PrepareRemove(paths...)
		if _, e := readProviderResource(body, pe, resourceDelete, paths); e != nil {
			return nil, e
		}
		for _, p := range paths {
			if e := requireGone(pcs, p, "delete"); e != nil {
				return nil, e
			}
		}
		return map[string]any{"status": "success"}, nil
	case "mkdir":
		var a struct {
			Path string `json:"path"`
		}
		if e := decodeArgs(r.Args, &a); e != nil {
			return nil, e
		}
		p, e := cleanRemote(a.Path, false)
		if e != nil {
			return nil, e
		}
		if e = ensureAbsent(pcs, p); e != nil {
			return nil, e
		}
		body, pe := pcs.PrepareMkdir(p)
		if _, e := readProviderResource(body, pe, resourceMkdir, []string{p}); e != nil {
			return nil, e
		}
		files, e := metadata(pcs, []string{p})
		return map[string]any{"status": "success", "files": files}, e
	case "copy", "move":
		var a struct {
			Source      string `json:"source"`
			Destination string `json:"destination"`
		}
		if e := decodeArgs(r.Args, &a); e != nil {
			return nil, e
		}
		src, e := cleanRemote(a.Source, false)
		if e != nil {
			return nil, e
		}
		dst, e := cleanRemote(a.Destination, false)
		if e != nil {
			return nil, e
		}
		if src == dst || strings.HasPrefix(dst, src+"/") {
			return nil, fail("invalid_input", "input", "目标路径不能与源路径相同或位于源目录内")
		}
		before, e := metadata(pcs, []string{src})
		if e != nil {
			return nil, e
		}
		if len(before) != 1 || before[0].Path != src {
			return nil, fail("invalid_provider_response", "provider", "百度网盘源文件信息不完整")
		}
		if e = ensureAbsent(pcs, dst); e != nil {
			return nil, e
		}
		item := &baidupcs.CpMvJSON{From: src, To: dst}
		var body io.ReadCloser
		var pe pcserror.Error
		if r.Operation == "copy" {
			body, pe = pcs.PrepareCopy(item)
		} else {
			body, pe = pcs.PrepareMove(item)
		}
		if _, e = readProviderResource(body, pe, resourceCopyMove, []string{src, dst}); e != nil {
			return nil, e
		}
		files, e := metadata(pcs, []string{dst})
		if e != nil {
			return nil, e
		}
		if len(files) != 1 || files[0].Path != dst || !sameResource(before[0], files[0]) {
			return nil, fail(r.Operation+"_verification_failed", "provider", "百度网盘目标文件大小、校验值或类型不一致，请刷新核验")
		}
		if r.Operation == "move" {
			if e := requireGone(pcs, src, "move"); e != nil {
				return nil, e
			}
		} else {
			current, e := metadata(pcs, []string{src})
			if e != nil {
				return nil, e
			}
			if len(current) != 1 || !sameResource(before[0], current[0]) {
				return nil, fail("copy_verification_failed", "provider", "百度网盘复制后源文件发生变化，请刷新核验")
			}
		}
		return map[string]any{"status": "success", "files": files}, nil
	case "upload":
		return uploadFile(pcs, r.Args)
	case "download":
		return downloadFile(pcs, r.Args)
	case "share-create":
		var a struct {
			Paths    []string `json:"paths"`
			Password string   `json:"password"`
			Period   int      `json:"period"`
		}
		if e := decodeArgs(r.Args, &a); e != nil {
			return nil, e
		}
		paths, e := cleanPaths(a.Paths)
		if e != nil {
			return nil, e
		}
		if a.Period != 0 && a.Period != 1 && a.Period != 7 && a.Period != 30 {
			return nil, fail("invalid_input", "input", "分享有效期须为永久、1 天、7 天或 30 天")
		}
		if a.Password != "" && !validPassword(a.Password) {
			return nil, fail("invalid_input", "input", "提取码须为四位字母或数字")
		}
		s, pe := pcs.ShareSet(paths, &baidupcs.ShareOption{Password: a.Password, Period: a.Period})
		if pe != nil {
			return nil, providerFailure(pe)
		}
		if s == nil || s.ShareID <= 0 || !validShareURL(s.Link) {
			return nil, fail("invalid_provider_response", "provider", "百度网盘未返回有效分享链接")
		}
		return map[string]any{"id": strconv.FormatInt(s.ShareID, 10), "share_url": s.Link, "passcode": s.Pwd, "period": a.Period}, nil
	case "share-list":
		return listShares(pcs, r.Args)
	case "share-detail":
		var a struct {
			ID string `json:"id"`
		}
		if e := decodeArgs(r.Args, &a); e != nil {
			return nil, e
		}
		id, e := strconv.ParseInt(a.ID, 10, 64)
		if e != nil || id <= 0 {
			return nil, fail("invalid_input", "input", "分享标识格式错误")
		}
		info, pe := pcs.ShareSURLInfo(id)
		if pe != nil {
			return nil, providerFailure(pe)
		}
		if info == nil {
			return nil, fail("invalid_provider_response", "provider", "百度网盘未返回有效分享信息")
		}
		link, e := shareURLFromShort(info.ShortURL)
		if e != nil {
			return nil, e
		}
		if info.Pwd != "" && !validPassword(info.Pwd) {
			return nil, fail("invalid_provider_response", "provider", "百度网盘返回的提取码格式错误")
		}
		return map[string]any{"id": a.ID, "pwd": info.Pwd, "passcode": info.Pwd, "shorturl": info.ShortURL, "share_url": link, "details_available": true}, nil
	case "share-cancel":
		var a struct {
			IDs []string `json:"ids"`
		}
		if e := decodeArgs(r.Args, &a); e != nil {
			return nil, e
		}
		if len(a.IDs) == 0 || len(a.IDs) > maxPaths {
			return nil, fail("invalid_input", "input", "请选择 1 至 100 个分享")
		}
		ids := make([]int64, 0, len(a.IDs))
		for _, s := range a.IDs {
			n, e := strconv.ParseInt(s, 10, 64)
			if e != nil || n <= 0 {
				return nil, fail("invalid_input", "input", "分享标识格式错误")
			}
			ids = append(ids, n)
		}
		body, pe := pcs.PrepareShareCancel(ids)
		if _, e := readProvider(body, pe); e != nil {
			return nil, e
		}
		return map[string]any{"status": "success"}, nil
	case "share-transfer":
		return nil, fail("unsupported_operation", "input", "当前百度适配器尚未提供分享转存接口")
	default:
		return nil, fail("unsupported_operation", "input", "不支持的百度网盘操作")
	}
}

func validPassword(s string) bool {
	if len(s) != 4 {
		return false
	}
	for _, c := range s {
		if !(c >= '0' && c <= '9' || c >= 'a' && c <= 'z' || c >= 'A' && c <= 'Z') {
			return false
		}
	}
	return true
}
func validShareURL(s string) bool {
	u, e := url.Parse(s)
	return e == nil && u.Scheme == "https" && u.Host == "pan.baidu.com" && strings.HasPrefix(u.Path, "/s/")
}

func shareURLFromShort(s string) (string, error) {
	if s == "" {
		return "", nil
	}
	if strings.HasPrefix(s, "https://") || strings.HasPrefix(s, "http://") {
		u, e := url.Parse(s)
		if e != nil || u.User != nil || u.Host != "pan.baidu.com" || !strings.HasPrefix(u.Path, "/s/") {
			return "", fail("invalid_provider_response", "provider", "百度网盘分享地址格式错误")
		}
		u.Scheme = "https"
		return u.String(), nil
	}
	if len(s) > 128 {
		return "", fail("invalid_provider_response", "provider", "百度网盘分享地址格式错误")
	}
	for _, c := range s {
		if !(c >= '0' && c <= '9' || c >= 'a' && c <= 'z' || c >= 'A' && c <= 'Z' || c == '_' || c == '-') {
			return "", fail("invalid_provider_response", "provider", "百度网盘分享地址格式错误")
		}
	}
	return "https://pan.baidu.com/s/" + s, nil
}

func listShares(pcs *baidupcs.BaiduPCS, args json.RawMessage) (any, error) {
	var a struct {
		Page           int  `json:"page"`
		IncludeDetails bool `json:"include_details"`
	}
	if e := decodeArgs(args, &a); e != nil {
		return nil, e
	}
	page, _, e := pageArgs(a.Page, 100)
	if e != nil {
		return nil, e
	}
	rows, pe := pcs.ShareList(page)
	if pe != nil {
		return nil, providerFailure(pe)
	}
	shares := make([]map[string]any, 0, len(rows))
	deadline := time.Now().Add(30 * time.Second)
	requested := 0
	for _, s := range rows {
		if s == nil {
			continue
		}
		ids := make([]string, 0, len(s.FsIds))
		for _, id := range s.FsIds {
			ids = append(ids, strconv.FormatInt(id, 10))
		}
		x := map[string]any{"shareId": strconv.FormatInt(s.ShareID, 10), "fsIds": ids, "shortlink": s.Shortlink, "status": s.Status, "public": s.Public, "typicalCategory": s.TypicalCategory, "typicalPath": s.TypicalPath, "expiredType": s.ExpireType, "expiredTime": s.ExpireTime, "vCnt": s.ViewCount, "pwd": "", "details_available": false}
		if a.IncludeDetails && s.Public == 0 && s.Status == 0 && s.ExpireType != -1 && requested < 5 && time.Now().Before(deadline) {
			remaining := time.Until(deadline)
			client := pcs.GetClient()
			old := client.Timeout
			client.SetTimeout(remaining)
			info, e := pcs.ShareSURLInfo(s.ShareID)
			client.SetTimeout(old)
			requested++
			if e == nil && info != nil {
				x["pwd"] = info.Pwd
				x["shorturl"] = info.ShortURL
				x["details_available"] = true
			}
		}
		shares = append(shares, x)
	}
	return map[string]any{"shares": shares, "has_more": len(rows) >= 100}, nil
}
