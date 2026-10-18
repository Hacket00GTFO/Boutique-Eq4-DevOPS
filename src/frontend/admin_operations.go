package main

import (
	"encoding/json"
	"net/http"
	"net/url"
	"os"
)

type operationsVault struct {
	Reachable   bool   `json:"reachable"`
	Initialized *bool  `json:"initialized"`
	Sealed      *bool  `json:"sealed"`
	Standby     *bool  `json:"standby"`
	Version     string `json:"version"`
	ReceivedAt  string `json:"received_at"`
	AgeSeconds  int    `json:"age_seconds"`
	Stale       bool   `json:"stale"`
}

func (v operationsVault) State() string {
	if v.Stale {
		return "Lectura antigua"
	}
	if !v.Reachable {
		return "Sin conexión"
	}
	if v.Initialized == nil || !*v.Initialized {
		return "Sin inicializar"
	}
	if v.Sealed == nil || *v.Sealed {
		return "Sellado"
	}
	if v.Standby != nil && *v.Standby {
		return "En espera"
	}
	return "Operativo"
}

type operationsEvent struct {
	Type    string `json:"type"`
	Label   string `json:"label"`
	Host    string `json:"host"`
	Elapsed int    `json:"elapsed"`
}
type operationsCounters struct {
	OK          int `json:"ok"`
	Changed     int `json:"changed"`
	Failed      int `json:"failed"`
	Unreachable int `json:"unreachable"`
	Skipped     int `json:"skipped"`
}
type operationsRun struct {
	ID         string             `json:"id"`
	Playbook   string             `json:"playbook"`
	Status     string             `json:"status"`
	StartedAt  string             `json:"started_at"`
	ReceivedAt string             `json:"received_at"`
	AgeSeconds int                `json:"age_seconds"`
	Elapsed    int                `json:"elapsed"`
	Stale      bool               `json:"stale"`
	Counters   operationsCounters `json:"counters"`
	Events     []operationsEvent  `json:"events"`
}

func (r operationsRun) State() string {
	if r.Status == "running" && r.Stale {
		return "Sin actualizaciones"
	}
	switch r.Status {
	case "running":
		return "En ejecución"
	case "succeeded":
		return "Completada"
	case "failed":
		return "Fallida"
	case "unreachable":
		return "Host inaccesible"
	default:
		return "Sin datos"
	}
}

type operationsData struct {
	Snapshot *operationsVault `json:"snapshot"`
	Runs     []operationsRun  `json:"runs"`
	Selected *operationsRun   `json:"selected"`
}

func operationsLoad(r *http.Request, kind string) (operationsData, error) {
	out := operationsData{Runs: []operationsRun{}}
	if !adminBackendEnabled() {
		return out, nil
	}
	path := "/api/v1/operations/" + kind
	if kind == "ansible" && r.URL.Query().Get("run_id") != "" {
		path += "?run_id=" + url.QueryEscape(r.URL.Query().Get("run_id"))
	}
	err := adminAPI(r.Context(), http.MethodGet, path, adminSession(r), nil, &out)
	return out, err
}

func vaultPublicURL() string {
	value := os.Getenv("VAULT_PUBLIC_UI_URL")
	parsed, err := url.Parse(value)
	if err != nil || parsed.Scheme != "https" || parsed.Host == "" || parsed.User != nil || parsed.RawQuery != "" || parsed.Fragment != "" {
		return ""
	}
	return value
}

func (fe *frontendServer) adminVaultOperation(w http.ResponseWriter, r *http.Request) {
	fe.adminOperationPage(w, r, "vault", "Operación de Vault")
}
func (fe *frontendServer) adminAnsibleOperation(w http.ResponseWriter, r *http.Request) {
	fe.adminOperationPage(w, r, "ansible", "Ejecuciones de Ansible")
}
func (fe *frontendServer) adminOperationPage(w http.ResponseWriter, r *http.Request, kind, title string) {
	data, err := operationsLoad(r, kind)
	if err != nil {
		adminFailure(w, r, err)
		return
	}
	w.Header().Set("Cache-Control", "no-store")
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	if err := adminTemplates.ExecuteTemplate(w, "admin_"+kind, injectAdminData(r, map[string]interface{}{
		"title": title, "active": kind, "operations": true, "ops": data, "vault_ui_url": vaultPublicURL(),
	})); err != nil {
		http.Error(w, "No se pudo mostrar la operación", http.StatusInternalServerError)
	}
}
func (fe *frontendServer) adminVaultOperationJSON(w http.ResponseWriter, r *http.Request) {
	fe.adminOperationJSON(w, r, "vault")
}
func (fe *frontendServer) adminAnsibleOperationJSON(w http.ResponseWriter, r *http.Request) {
	fe.adminOperationJSON(w, r, "ansible")
}
func (fe *frontendServer) adminOperationJSON(w http.ResponseWriter, r *http.Request, kind string) {
	w.Header().Set("Cache-Control", "no-store")
	w.Header().Set("Content-Type", "application/json")
	data, err := operationsLoad(r, kind)
	if err != nil {
		code := http.StatusServiceUnavailable
		if failure, ok := err.(*adminAPIError); ok {
			code = failure.Status
		}
		w.WriteHeader(code)
		_ = json.NewEncoder(w).Encode(map[string]string{"message": "No se pudo actualizar la operación. Comprueba tu sesión y la conexión."})
		return
	}
	_ = json.NewEncoder(w).Encode(data)
}
