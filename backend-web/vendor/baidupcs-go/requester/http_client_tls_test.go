package requester

import (
	"io"
	"log"
	"net/http"
	"net/http/httptest"
	"testing"
)

// Xianyu integration regression: all requests stay on the local TLS test server.
func TestTLSVerificationDefaultsAndExplicitSetting(t *testing.T) {
	server := httptest.NewUnstartedServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, _ = io.WriteString(w, "local-test")
	}))
	server.Config.ErrorLog = log.New(io.Discard, "", 0)
	server.StartTLS()
	defer server.Close()

	client := NewHTTPClient()
	client.lazyInit()
	// Isolate the local test from the caller's proxy configuration.
	client.transport.Proxy = nil
	if !client.https || client.transport.TLSClientConfig.InsecureSkipVerify {
		t.Fatal("new client must verify TLS certificates")
	}
	if response, err := client.Req(http.MethodGet, server.URL, nil, nil); err == nil {
		response.Body.Close()
		t.Fatal("default client accepted an untrusted self-signed certificate")
	}

	client.SetHTTPSecure(false)
	response, err := client.Req(http.MethodGet, server.URL, nil, nil)
	if err != nil {
		t.Fatalf("explicit insecure setting was not honored: %v", err)
	}
	response.Body.Close()
	client.transport.CloseIdleConnections()

	client.SetHTTPSecure(true)
	if client.transport.TLSClientConfig.InsecureSkipVerify {
		t.Fatal("enabling TLS validation left certificate verification disabled")
	}
	if response, err := client.Req(http.MethodGet, server.URL, nil, nil); err == nil {
		response.Body.Close()
		t.Fatal("reenabled TLS validation accepted an untrusted certificate")
	}
}
