package main

import (
	"bytes"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/gorilla/mux"
)

func TestOperationsPagesRenderHonestEmptyStatesAndNavigation(t *testing.T) {
	t.Setenv("ADMIN_DATA_MODE", "")
	fe := &frontendServer{}
	for _, tc := range []struct {
		path, text string
		handler    http.HandlerFunc
	}{
		{"/admin/vault", "Sin datos de Vault", fe.adminVaultOperation},
		{"/admin/ansible", "Aún no hay ejecuciones registradas", fe.adminAnsibleOperation},
	} {
		t.Run(tc.path, func(t *testing.T) {
			response := httptest.NewRecorder()
			tc.handler(response, httptest.NewRequest(http.MethodGet, tc.path, nil))
			if response.Code != 200 || !strings.Contains(response.Body.String(), tc.text) {
				t.Fatalf("Page failed: %d", response.Code)
			}
			for _, marker := range []string{"/admin/vault", "/admin/ansible", "operations.js", "operations.css"} {
				if !strings.Contains(response.Body.String(), marker) {
					t.Errorf("Missing %s", marker)
				}
			}
			if response.Header().Get("Cache-Control") != "no-store" {
				t.Error("Operations must not be cached")
			}
		})
	}
}

func TestOperationsRoutesRequireLogin(t *testing.T) {
	t.Setenv("ADMIN_DATA_MODE", "")
	fe := &frontendServer{}
	router := mux.NewRouter()
	admin := router.PathPrefix("/admin").Subrouter()
	admin.Use(requireAdminAuth)
	admin.HandleFunc("/vault", fe.adminVaultOperation)
	admin.HandleFunc("/ansible", fe.adminAnsibleOperation)
	admin.HandleFunc("/operaciones/vault.json", fe.adminVaultOperationJSON)
	for _, path := range []string{"/admin/vault", "/admin/ansible", "/admin/operaciones/vault.json"} {
		response := httptest.NewRecorder()
		router.ServeHTTP(response, httptest.NewRequest(http.MethodGet, path, nil))
		if response.Code != http.StatusSeeOther || !strings.HasSuffix(response.Header().Get("Location"), "/admin/login") {
			t.Errorf("Unprotected route: %s", path)
		}
	}
}

func TestOperationsTemplatesEscapeTerminalMessages(t *testing.T) {
	var output bytes.Buffer
	request := httptest.NewRequest(http.MethodGet, "/admin/ansible", nil)
	data := operationsData{Selected: &operationsRun{ID: "test", Playbook: "site.yml", Events: []operationsEvent{{Type: "ok", Label: "<script>alert(1)</script>"}}}}
	err := adminTemplates.ExecuteTemplate(&output, "admin_ansible", injectAdminData(request, map[string]interface{}{"active": "ansible", "operations": true, "ops": data}))
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(output.String(), "<script>alert(1)</script>") || !strings.Contains(output.String(), "&lt;script&gt;") {
		t.Fatal("Terminal messages must be escaped")
	}
}

func TestOperationsDetailsRenderDurationsZeroExitCodesAndSafeErrors(t *testing.T) {
	duration, rc := 1250, 0
	event := operationsEvent{Type: "failed", Label: "Check service", Module: "ansible.builtin.command",
		Role: "vault_service", DurationMS: &duration, RC: &rc, Ignored: true,
		DiagnosticText: "<script>unsafe</script>", Hint: "Check permissions"}
	var output bytes.Buffer
	data := operationsData{Selected: &operationsRun{ID: "test", Playbook: "site.yml", Events: []operationsEvent{event}}}
	err := adminTemplates.ExecuteTemplate(&output, "admin_ansible", injectAdminData(httptest.NewRequest(http.MethodGet, "/admin/ansible", nil), map[string]interface{}{"active": "ansible", "operations": true, "ops": data}))
	if err != nil {
		t.Fatal(err)
	}
	for _, expected := range []string{"ansible.builtin.command", "vault_service", "1.250 s", "salida: 0", "error ignorado", "&lt;script&gt;", "Check permissions"} {
		if !strings.Contains(output.String(), expected) {
			t.Errorf("Missing diagnostic detail: %s", expected)
		}
	}
	if strings.Contains(output.String(), "<script>unsafe</script>") {
		t.Fatal("Diagnostic HTML was not escaped")
	}
}

func TestVaultStatusCannotTreatOldReadingsAsOperational(t *testing.T) {
	value := true
	sealed := false
	if got := (operationsVault{Reachable: true, Initialized: &value, Sealed: &sealed}).State(); got != "Operativo" {
		t.Fatal(got)
	}
	if got := (operationsVault{Reachable: true, Initialized: &value, Sealed: &sealed, Stale: true}).State(); got == "Operativo" {
		t.Fatal("Stale status presented as live")
	}
	for _, unsafe := range []string{"javascript:alert(1)", "http://vault/ui/", "https://user:password@vault/ui/"} {
		t.Setenv("VAULT_PUBLIC_UI_URL", unsafe)
		if vaultPublicURL() != "" {
			t.Fatal("Unsafe UI link")
		}
	}
}
