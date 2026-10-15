package main

import (
	"bytes"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/qjfoidnh/BaiduPCS-Go/baidupcs"
)

type transportFunc func(*http.Request) (*http.Response, error)

func (f transportFunc) RoundTrip(r *http.Request) (*http.Response, error) { return f(r) }

func fixturePCS(t *testing.T, handler func(*http.Request) string) *baidupcs.BaiduPCS {
	t.Helper()
	pcs, e := newPCS(credentials{BDUSS: "contract-fixture-only"})
	if e != nil {
		t.Fatal(e)
	}
	pcs.GetClient().Transport = &safeTransport{maxBody: maxJSONBytes, next: transportFunc(func(r *http.Request) (*http.Response, error) {
		return &http.Response{StatusCode: 200, Header: http.Header{"Content-Type": []string{"application/json"}}, Body: io.NopCloser(strings.NewReader(handler(r))), Request: r}, nil
	})}
	return pcs
}

func TestRequestBoundariesAndNoCredentialLeak(t *testing.T) {
	for _, text := range []string{
		`{"operation":"profile","credentials":{},"args":{}} {}`,
		`{"operation":"profile","unexpected":"sensitive-test-value"}`,
		strings.Repeat("x", maxInputBytes+1),
	} {
		_, e := readRequest(strings.NewReader(text))
		if e == nil {
			t.Fatal("invalid request accepted")
		}
		b, _ := json.Marshal(envelope(nil, e))
		if strings.Contains(string(b), "sensitive-test-value") {
			t.Fatal("request echoed into error")
		}
	}
	if _, e := newPCS(credentials{}); e == nil {
		t.Fatal("missing credentials accepted")
	}
	if _, e := newPCS(credentials{BDUSS: "credential\r\nunsafe"}); e == nil {
		t.Fatal("header injection accepted")
	}
	r, e := readRequest(strings.NewReader(`{"operation":"profile","credentials":{"uid":0},"args":{}}`))
	if e != nil {
		t.Fatal(e)
	}
	if _, e = newPCS(r.Credentials); e == nil {
		t.Fatal("uid zero treated as authorization")
	}
	b, _ := json.Marshal(envelope(nil, errors.New("https://pan.baidu.com/?BDUSS=sensitive-test-value")))
	if strings.Contains(string(b), "sensitive-test-value") || strings.Contains(string(b), "https://") {
		t.Fatal("network error leaked URL or credential")
	}
}

func TestSafeTransportBlocksHTTPAndUnrelatedHosts(t *testing.T) {
	calls := 0
	x := &safeTransport{next: transportFunc(func(r *http.Request) (*http.Response, error) { calls++; return nil, errors.New("offline fixture") })}
	for _, s := range []string{"http://pan.baidu.com/api/list", "https://pan.baidu.com.evil.invalid/", "https://127.0.0.1/", "https://user:password@pan.baidu.com/", "https://pan.baidu.com:8443/"} {
		r, _ := http.NewRequest("GET", s, nil)
		if _, e := x.RoundTrip(r); e == nil {
			t.Fatal("unsafe URL accepted")
		}
	}
	if calls != 0 {
		t.Fatal("unsafe request reached transport")
	}
	r, _ := http.NewRequest("GET", "https://pan.baidu.com/api/list", nil)
	_, _ = x.RoundTrip(r)
	if calls != 1 {
		t.Fatal("allowed Baidu URL blocked")
	}
	pcs, e := newPCS(credentials{BDUSS: "contract-fixture-only"})
	if e != nil {
		t.Fatal(e)
	}
	inner := pcs.GetClient().Transport.(*safeTransport).next.(*http.Transport)
	if inner.TLSClientConfig == nil || inner.TLSClientConfig.InsecureSkipVerify {
		t.Fatal("TLS verification disabled")
	}
}

func TestBoundedProviderBody(t *testing.T) {
	for _, n := range []int{3, 4} {
		b := &boundedBody{ReadCloser: io.NopCloser(strings.NewReader(strings.Repeat("x", n))), left: 3}
		got, e := io.ReadAll(b)
		if n == 3 && (e != nil || len(got) != 3) {
			t.Fatal("exact limit rejected")
		}
		if n == 4 && e == nil {
			t.Fatal("oversized response accepted")
		}
	}
	_, e := readProvider(io.NopCloser(strings.NewReader(`{"errno":-6,"errmsg":"BDUSS=sensitive-test-value"}`)), nil)
	if e == nil || envelope(nil, e).Kind != "auth" || strings.Contains(e.Error(), "sensitive-test-value") {
		t.Fatal("unsafe auth error")
	}
	for _, response := range []string{`{}`, `{"errno":"invalid"}`, `{"errno":null}`} {
		if _, e := readProvider(io.NopCloser(strings.NewReader(response)), nil); e == nil {
			t.Fatal("invalid provider status accepted")
		}
	}
}

func TestProfileRequiresUKAndRootRead(t *testing.T) {
	for _, uk := range []string{"0", "9007199254740993"} {
		rootRead := false
		pcs := fixturePCS(t, func(r *http.Request) string {
			switch r.URL.Path {
			case "/api/user/getinfo":
				return `{"errno":0,"records":[{"uk":` + uk + `,"uname":"Fixture user"}]}`
			case "/api/list":
				rootRead = true
				if r.URL.Query().Get("dir") != "/" {
					t.Fatal("profile did not validate root")
				}
				return `{"errno":0,"list":[]}`
			default:
				return `{"error_code":0,"quota":1024,"used":64}`
			}
		})
		data, e := dispatch(pcs, &request{Operation: "profile", Args: json.RawMessage(`{}`)})
		if uk == "0" {
			if e == nil || rootRead {
				t.Fatal("zero UK accepted or unnecessary root request")
			}
			continue
		}
		if e != nil {
			t.Fatal(e)
		}
		if !rootRead || data.(map[string]any)["uid"] != uk {
			t.Fatal("UK precision/root verification lost")
		}
	}
	pcs := fixturePCS(t, func(r *http.Request) string {
		if r.URL.Path == "/api/user/getinfo" {
			return `{"errno":0,"records":[{"uk":12}]}`
		}
		return `{"errno":-6}`
	})
	if _, e := dispatch(pcs, &request{Operation: "profile", Args: json.RawMessage(`{}`)}); e == nil {
		t.Fatal("unreadable root accepted as authorization")
	}
}

func TestListUsesExactlyOneProviderPageAndStringIDs(t *testing.T) {
	calls := 0
	pcs := fixturePCS(t, func(r *http.Request) string {
		calls++
		if r.URL.Path != "/api/list" || r.URL.Query().Get("num") != "2" || r.URL.Query().Get("page") != "3" || r.URL.Query().Get("dir") != "/原有文件" {
			t.Fatal("wrong provider pagination")
		}
		return `{"errno":0,"list":[{"fs_id":9007199254740993,"path":"/原有文件/a.txt","server_filename":"a.txt","size":8,"isdir":0,"server_mtime":1700000000}]}`
	})
	data, e := dispatch(pcs, &request{Operation: "list", Args: json.RawMessage(`{"dir":"/原有文件","page":3,"page_size":2}`)})
	if e != nil {
		t.Fatal(e)
	}
	if calls != 1 {
		t.Fatal("list read the whole directory")
	}
	d := data.(map[string]any)
	files := d["files"].([]fileInfo)
	if files[0].FsID != "9007199254740993" || files[0].Mtime != 1700000000 || d["has_more"] != false {
		t.Fatal("incorrect list normalization")
	}
}

func TestSearchAllowsCoordinatorBatchAndRefusesSilentLimit(t *testing.T) {
	pcs := fixturePCS(t, func(r *http.Request) string {
		if r.URL.Query().Get("method") != "search" || r.URL.Query().Get("re") != "1" {
			t.Fatal("incorrect upstream recursive search")
		}
		return `{"error_code":0,"list":[{"fs_id":1,"path":"/folder/one.txt","server_filename":"one.txt","size":1,"isdir":0},{"fs_id":2,"path":"/folder/two.txt","server_filename":"two.txt","size":2,"isdir":0}]}`
	})
	data, e := dispatch(pcs, &request{Operation: "search", Args: json.RawMessage(`{"dir":"/folder","keyword":"txt","page":1,"page_size":10000,"max_items":10000}`)})
	if e != nil {
		t.Fatal(e)
	}
	if data.(map[string]any)["total"] != 2 || len(data.(map[string]any)["files"].([]fileInfo)) != 2 {
		t.Fatal("coordinator search lost items")
	}
	_, e = dispatch(pcs, &request{Operation: "search", Args: json.RawMessage(`{"dir":"/folder","keyword":"txt","page":1,"page_size":1,"max_items":2}`)})
	if e == nil || envelope(nil, e).ErrorCode != "search_limit_exceeded" {
		t.Fatal("search silently truncated at limit")
	}
}

func TestMutationCannotOverwriteAndCopyPrepareErrorIsPreserved(t *testing.T) {
	calls := 0
	pcs := fixturePCS(t, func(r *http.Request) string {
		calls++
		if r.URL.Query().Get("app_id") != "266719" {
			t.Fatal("PCS metadata did not use the maintained upstream application ID")
		}
		if calls == 1 {
			return `{"error_code":0,"list":[{"fs_id":2,"path":"/from","server_filename":"from","isdir":0,"size":1}]}`
		}
		return `{"error_code":0,"list":[{"fs_id":1,"path":"/exists","server_filename":"exists","isdir":0,"size":1}]}`
	})
	if _, e := dispatch(pcs, &request{Operation: "move", Args: json.RawMessage(`{"source":"/from","destination":"/exists"}`)}); e == nil || envelope(nil, e).ErrorCode != "already_exists" {
		t.Fatal("existing destination accepted")
	}
	calls = 0
	pcs = fixturePCS(t, func(r *http.Request) string {
		calls++
		if r.URL.Query().Get("method") == "meta" {
			if calls == 1 {
				return `{"error_code":0,"list":[{"fs_id":2,"path":"/from","server_filename":"from","isdir":0,"size":1}]}`
			}
			return `{"error_code":31066}`
		}
		return `{"error_code":31023,"error_msg":"sensitive-test-value"}`
	})
	_, e := dispatch(pcs, &request{Operation: "copy", Args: json.RawMessage(`{"source":"/from","destination":"/new"}`)})
	if e == nil || envelope(nil, e).Kind != "auth" || strings.Contains(e.Error(), "sensitive-test-value") {
		t.Fatal("copy provider error dropped")
	}
	for _, operation := range []string{"mkdir", "delete", "copy", "move", "share-create"} {
		_, e := dispatch(pcs, &request{Operation: operation, Args: json.RawMessage(`{"path":"/","paths":["/"],"source":"/","destination":"/new"}`)})
		if e == nil {
			t.Fatalf("root mutation accepted: %s", operation)
		}
	}
}

func TestNativeMkdirAndMetaResourceShapesWithoutStatus(t *testing.T) {
	for _, mkdirResponse := range []string{
		`{"fs_id":3,"path":"/test","isdir":1,"status":0,"ctime":1,"mtime":1,"request_id":1}`,
		`{"fs_id":3,"path":"/wrong","isdir":1,"status":0}`,
		`{"fs_id":3,"path":"/test","isdir":0,"status":0}`,
		`{"fs_id":0,"path":"/test","isdir":1,"status":0}`,
		`{"fs_id":3,"path":"/test","isdir":1,"status":4}`,
	} {
		calls := 0
		pcs := fixturePCS(t, func(r *http.Request) string {
			calls++
			if calls == 1 {
				return `{"error_code":31066,"request_id":1}`
			}
			if r.URL.Query().Get("method") == "mkdir" {
				return mkdirResponse
			}
			return `{"list":[{"fs_id":3,"path":"/test","server_filename":"test","isdir":1,"status":0,"size":0,"mtime":1}],"request_id":1}`
		})
		data, e := dispatch(pcs, &request{Operation: "mkdir", Args: json.RawMessage(`{"path":"/test"}`)})
		if strings.Contains(mkdirResponse, `"ctime"`) {
			if e != nil || calls != 3 || data.(map[string]any)["files"].([]fileInfo)[0].Path != "/test" {
				t.Fatal("confirmed native resource response rejected")
			}
		} else if e == nil || calls != 2 {
			t.Fatal("incorrect native mkdir resource accepted")
		}
	}
	wrong := io.NopCloser(strings.NewReader(`{"list":[{"fs_id":3,"path":"/wrong","isdir":1}],"request_id":1}`))
	if _, e := readProviderResource(wrong, nil, resourceMeta, []string{"/test"}); e == nil {
		t.Fatal("wrong-path metadata accepted without status")
	}
	if _, e := readProvider(io.NopCloser(strings.NewReader(`{"fs_id":3,"path":"/test","isdir":1}`)), nil); e == nil {
		t.Fatal("resource exception widened to unrelated operations")
	}
}

func ackFixture(t *testing.T, scenario string) (*baidupcs.BaiduPCS, map[string]map[string]any) {
	t.Helper()
	files := map[string]map[string]any{"/from": {"fs_id": 1, "path": "/from", "server_filename": "from", "size": 3, "isdir": 0, "md5": "900150983cd24fb0d6963f7d28e17f72"}}
	deleted := false
	pcs := fixturePCS(t, func(r *http.Request) string {
		if r.ParseMultipartForm(1024*1024) != nil {
			t.Fatal("invalid fixture multipart request")
		}
		var params struct {
			List []struct {
				Path string `json:"path"`
				From string `json:"from"`
				To   string `json:"to"`
			} `json:"list"`
		}
		if json.Unmarshal([]byte(r.FormValue("param")), &params) != nil || len(params.List) == 0 {
			t.Fatal("invalid fixture PCS parameter")
		}
		item := params.List[0]
		switch r.URL.Query().Get("method") {
		case "meta":
			if scenario == "post_delete_auth" && deleted {
				return `{"error_code":31045}`
			}
			rows := []map[string]any{}
			for _, p := range params.List {
				row, ok := files[p.Path]
				if !ok {
					return `{"error_code":31066}`
				}
				rows = append(rows, row)
			}
			b, _ := json.Marshal(map[string]any{"request_id": 1, "list": rows})
			return string(b)
		case "copy", "move":
			source := files[item.From]
			target := map[string]any{}
			for k, v := range source {
				target[k] = v
			}
			target["path"] = item.To
			target["server_filename"] = item.To[1:]
			target["fs_id"] = 2
			if scenario == "different_md5" {
				target["md5"] = "00000000000000000000000000000000"
			}
			files[item.To] = target
			if r.URL.Query().Get("method") == "move" && scenario != "retained_source" {
				delete(files, item.From)
			}
			from := item.From
			if scenario == "wrong_ack" {
				from = "/unrelated"
			}
			b, _ := json.Marshal(map[string]any{"request_id": 1, "extra": map[string]any{"list": []map[string]any{{"from": from, "to": item.To}}}})
			return string(b)
		case "delete":
			if scenario != "retained_delete" {
				for _, p := range params.List {
					delete(files, p.Path)
				}
			}
			deleted = true
			return `{"request_id":1}`
		default:
			t.Fatal("unexpected fixture operation")
			return ""
		}
	})
	return pcs, files
}

func TestNativeCopyMoveDeleteACKVerifiesActualState(t *testing.T) {
	pcs, files := ackFixture(t, "")
	for _, step := range []struct{ operation, args string }{
		{"copy", `{"source":"/from","destination":"/copy"}`},
		{"move", `{"source":"/copy","destination":"/moved"}`},
		{"delete", `{"paths":["/moved"]}`},
	} {
		data, e := dispatch(pcs, &request{Operation: step.operation, Args: json.RawMessage(step.args)})
		if e != nil || data.(map[string]any)["status"] != "success" {
			t.Fatalf("native ACK state verification failed for %s", step.operation)
		}
	}
	if len(files) != 1 || files["/from"] == nil {
		t.Fatal("primary fixture file was changed")
	}
	for _, scenario := range []string{"wrong_ack", "retained_source", "different_md5"} {
		pcs, _ := ackFixture(t, scenario)
		_, e := dispatch(pcs, &request{Operation: "move", Args: json.RawMessage(`{"source":"/from","destination":"/moved"}`)})
		if e == nil {
			t.Fatalf("invalid move was reported successful: %s", scenario)
		}
	}
	for _, scenario := range []string{"retained_delete", "post_delete_auth"} {
		pcs, _ := ackFixture(t, scenario)
		_, e := dispatch(pcs, &request{Operation: "delete", Args: json.RawMessage(`{"paths":["/from"]}`)})
		if e == nil {
			t.Fatalf("unverified deletion was reported successful: %s", scenario)
		}
		if scenario == "post_delete_auth" && envelope(nil, e).Kind != "auth" {
			t.Fatal("auth failure was treated as absence")
		}
	}
	if _, e := readProviderResource(io.NopCloser(strings.NewReader(`{"request_id":1,"unexpected":"sensitive-test-value"}`)), nil, resourceDelete, []string{"/from"}); e == nil {
		t.Fatal("unknown ACK fields accepted")
	}
}

func TestNativeShareCreateAndListDoNotInventDates(t *testing.T) {
	pcs := fixturePCS(t, func(r *http.Request) string {
		if r.URL.Path == "/share/pset" {
			b, _ := io.ReadAll(r.Body)
			v, _ := url.ParseQuery(string(b))
			if v.Get("period") != "1" || v.Get("pwd") != "a123" || v.Get("path_list") != `["/file.txt"]` {
				t.Fatal("incorrect native share request")
			}
			return `{"errno":0,"shareid":9007199254740993,"link":"https://pan.baidu.com/s/fixture"}`
		}
		if r.URL.Path != "/share/record" {
			t.Fatal("unexpected share details lookup")
		}
		return `{"errno":0,"list":[{"shareId":9007199254740993,"fsIds":[9007199254740994],"shortlink":"https://pan.baidu.com/s/fixture","typicalPath":"/file.txt","expiredType":1,"expiredTime":86400,"status":0,"public":0}]}`
	})
	data, e := dispatch(pcs, &request{Operation: "share-create", Args: json.RawMessage(`{"paths":["/file.txt"],"password":"a123","period":1}`)})
	if e != nil {
		t.Fatal(e)
	}
	if data.(map[string]any)["id"] != "9007199254740993" {
		t.Fatal("share ID lost precision")
	}
	data, e = dispatch(pcs, &request{Operation: "share-list", Args: json.RawMessage(`{"page":1}`)})
	if e != nil {
		t.Fatal(e)
	}
	s := data.(map[string]any)["shares"].([]map[string]any)[0]
	if s["shareId"] != "9007199254740993" || s["expiredTime"] != int64(86400) || s["details_available"] != false {
		t.Fatal("incorrect native share normalization")
	}
	if _, ok := s["created_at"]; ok {
		t.Fatal("created_at was fabricated")
	}
	if _, e := dispatch(pcs, &request{Operation: "share-transfer", Args: json.RawMessage(`{}`)}); e == nil || envelope(nil, e).ErrorCode != "unsupported_operation" {
		t.Fatal("unimplemented transfer pretended to succeed")
	}
}

func TestShareDetailFetchesExistingNativePasswordOnce(t *testing.T) {
	calls := 0
	pcs := fixturePCS(t, func(r *http.Request) string {
		calls++
		if r.URL.Path != "/share/surlinfoinrecord" || r.URL.Query().Get("shareid") != "9007199254740993" {
			t.Fatal("incorrect native share detail request")
		}
		return `{"errno":0,"pwd":"a123","shorturl":"1fixture_abc-xyz"}`
	})
	data, e := dispatch(pcs, &request{Operation: "share-detail", Args: json.RawMessage(`{"id":"9007199254740993"}`)})
	if e != nil {
		t.Fatal(e)
	}
	d := data.(map[string]any)
	if calls != 1 || d["passcode"] != "a123" || d["share_url"] != "https://pan.baidu.com/s/1fixture_abc-xyz" || d["details_available"] != true {
		t.Fatal("existing share details incomplete")
	}
	if _, e := shareURLFromShort("https://unrelated.invalid/s/secret"); e == nil {
		t.Fatal("unrelated share URL accepted")
	}
}

func privateTestDir(t *testing.T) string {
	t.Helper()
	dir, e := filepath.EvalSymlinks(t.TempDir())
	if e != nil {
		t.Fatal(e)
	}
	return dir
}

func TestLocalFilesRejectSymlinksAndExistingDownloads(t *testing.T) {
	dir := privateTestDir(t)
	real := filepath.Join(dir, "real.txt")
	link := filepath.Join(dir, "link.txt")
	if os.WriteFile(real, []byte("fixture"), 0600) != nil || os.Symlink(real, link) != nil {
		t.Fatal("fixture setup failed")
	}
	if _, _, e := localRoot(link, true); e == nil {
		t.Fatal("symlink upload accepted")
	}
	if _, _, e := localRoot(real, false); e == nil {
		t.Fatal("existing download overwritten")
	}
	t.Setenv("BAIDUPCS_BRIDGE_FILE_ROOT", filepath.Join(dir, "unrelated"))
	if _, _, e := localRoot(real, true); e == nil {
		t.Fatal("file outside boundary accepted")
	}
	t.Setenv("BAIDUPCS_BRIDGE_FILE_ROOT", dir)
	root, _, e := localRoot(real, true)
	if e != nil {
		t.Fatal(e)
	}
	root.Close()
}

func TestFileHashAndUploadNeverUsesOverwritePolicy(t *testing.T) {
	dir := privateTestDir(t)
	file := filepath.Join(dir, "input.txt")
	content := []byte("upload fixture")
	if e := os.WriteFile(file, content, 0600); e != nil {
		t.Fatal(e)
	}
	f, e := os.Open(file)
	if e != nil {
		t.Fatal(e)
	}
	defer f.Close()
	hashes, e := hashFile(f, int64(len(content)))
	if e != nil {
		t.Fatal(e)
	}
	if len(hashes.blocks) != 1 || hashes.blocks[0] != hashes.full || hashes.slice != hashes.full {
		t.Fatal("incorrect file hashes")
	}
	checks := 0
	pcs := fixturePCS(t, func(r *http.Request) string {
		switch r.URL.Path {
		case "/api/precreate":
			return `{"errno":0,"return_type":1,"uploadid":"fixture-upload"}`
		case "/rest/2.0/pcs/superfile2":
			if r.URL.Query().Get("partseq") != "0" {
				t.Fatal("wrong upload part")
			}
			return `{"error_code":0,"md5":"` + hashes.full + `"}`
		case "/api/create":
			checks++
			b, _ := io.ReadAll(r.Body)
			v, _ := url.ParseQuery(string(b))
			if v.Get("rtype") != "0" {
				t.Fatal("overwrite upload policy used")
			}
			return `{"errno":0,"path":"/input.txt"}`
		default:
			checks++
			if checks == 1 {
				return `{"error_code":31066}`
			}
			return `{"error_code":0,"list":[{"fs_id":2,"path":"/input.txt","server_filename":"input.txt","size":14,"isdir":0,"md5":"` + hashes.full + `"}]}`
		}
	})
	args, _ := json.Marshal(map[string]any{"path": "/input.txt", "local_path": file})
	data, e := dispatch(pcs, &request{Operation: "upload", Args: args})
	if e != nil {
		t.Fatal(e)
	}
	if data.(map[string]any)["status"] != "success" || checks != 3 {
		t.Fatal("upload result not verified")
	}
	if !bytes.Equal(content, []byte("upload fixture")) {
		t.Fatal("fixture mutated")
	}
}
