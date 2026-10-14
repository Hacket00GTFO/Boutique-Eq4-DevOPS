// Copyright 2024 Google LLC
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//      http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

package main

import (
	"crypto/hmac"
	"github.com/google/uuid"
	"html/template"
	"net/http"
	"net/url"
	"strconv"
	"strings"

	"github.com/sirupsen/logrus"
)

// ---------------------------------------------------------------------------
// Admin panel — HTTP handlers
//
// Data comes from `adminData` (AdminDataProvider). Today it is mockAdminData;
// swap it for a gRPC/REST-backed implementation when the backend lands.
// See docs/admin-panel.md.
// ---------------------------------------------------------------------------

var (
	adminData AdminDataProvider = mockAdminData{}

	adminTemplates = template.Must(template.New("admin").
			Funcs(template.FuncMap{
			"statusClass": adminStatusClass,
			"joinInts":    joinInts,
			"joinSales": func(values []float64) string {
				parts := make([]string, len(values))
				for i, v := range values {
					parts[i] = strconv.FormatFloat(v, 'f', 2, 64)
				}
				return strings.Join(parts, ",")
			},
			"add": func(a, b int) int { return a + b },
			"pageURL": func(path string, q url.Values, page int) string {
				c := url.Values{}
				for k, v := range q {
					c[k] = append([]string(nil), v...)
				}
				c.Set("page", strconv.Itoa(page))
				return path + "?" + c.Encode()
			},
			"exportURL": func(q url.Values, resource string) string {
				c := url.Values{}
				for k, v := range q {
					c[k] = append([]string(nil), v...)
				}
				c.Set("resource", resource)
				return baseUrl + "/admin/export?" + c.Encode()
			},
		}).ParseGlob("templates/admin/*.html"))
)

func (fe *frontendServer) adminLoginViewHandler(w http.ResponseWriter, r *http.Request) {
	log := r.Context().Value(ctxKeyLog{}).(logrus.FieldLogger)
	if validAdminSession(r) {
		http.Redirect(w, r, baseUrl+"/admin", http.StatusSeeOther)
		return
	}
	loginCSRF := uuid.NewString()
	http.SetCookie(w, &http.Cookie{Name: "admin_login_csrf", Value: loginCSRF, Path: baseUrl + "/admin", HttpOnly: true, SameSite: http.SameSiteStrictMode, Secure: adminCookieSecure(), MaxAge: 600})
	if err := adminTemplates.ExecuteTemplate(w, "admin_login", injectAdminData(r, map[string]interface{}{
		"csrf_token":  loginCSRF,
		"login_email": "",
	})); err != nil {
		log.Error(err)
	}
}

func (fe *frontendServer) adminLoginSubmitHandler(w http.ResponseWriter, r *http.Request) {
	r.Body = http.MaxBytesReader(w, r.Body, 1<<20)
	log := r.Context().Value(ctxKeyLog{}).(logrus.FieldLogger)
	email := strings.TrimSpace(r.FormValue("email"))
	password := r.FormValue("password")
	if adminBackendEnabled() {
		c, err := r.Cookie("admin_login_csrf")
		if err != nil || !hmac.Equal([]byte(c.Value), []byte(r.FormValue("csrf_token"))) {
			http.Error(w, "Token CSRF inválido", http.StatusForbidden)
			return
		}
		var session struct {
			Token string `json:"token"`
		}
		if err := adminAPI(r.Context(), http.MethodPost, "/api/v1/auth/login", "", map[string]string{"email": email, "password": password}, &session); err != nil {
			if failure, ok := err.(*adminAPIError); ok && failure.Status == http.StatusUnauthorized {
				w.WriteHeader(http.StatusUnauthorized)
				_ = adminTemplates.ExecuteTemplate(w, "admin_login", injectAdminData(r, map[string]interface{}{"login_error": "Credenciales inválidas.", "login_email": email, "csrf_token": c.Value}))
				return
			}
			adminFailure(w, r, err)
			return
		}
		setAdminSessionCookie(w, session.Token)
		http.Redirect(w, r, baseUrl+"/admin", http.StatusSeeOther)
		return
	}

	if !checkAdminCredentials(email, password) {
		log.WithField("email", email).Warn("admin login failed")
		if err := adminTemplates.ExecuteTemplate(w, "admin_login", injectAdminData(r, map[string]interface{}{
			"login_error": "Credenciales inválidas. Inténtalo de nuevo.",
			"login_email": email,
		})); err != nil {
			log.Error(err)
		}
		return
	}

	setAdminSessionCookie(w, newAdminSession())
	log.WithField("email", email).Info("admin logged in")
	http.Redirect(w, r, baseUrl+"/admin", http.StatusSeeOther)
}

func (fe *frontendServer) adminLogoutHandler(w http.ResponseWriter, r *http.Request) {
	if adminBackendEnabled() && r.Method != http.MethodPost {
		http.Error(w, "Usa POST para cerrar sesión", http.StatusMethodNotAllowed)
		return
	}
	log := r.Context().Value(ctxKeyLog{}).(logrus.FieldLogger)
	if c, err := r.Cookie(adminCookieName); err == nil {
		if adminBackendEnabled() {
			if err := adminAPI(r.Context(), "POST", "/api/v1/auth/logout", c.Value, map[string]string{}, nil); err != nil {
				adminFailure(w, r, err)
				return
			}
		}
		destroyAdminSession(c.Value)
	}
	clearAdminSessionCookie(w)
	log.Debug("admin logged out")
	http.Redirect(w, r, baseUrl+"/admin/login", http.StatusSeeOther)
}

func (fe *frontendServer) adminDashboardHandler(w http.ResponseWriter, r *http.Request) {
	dashboard, err := loadDashboard(r)
	if err != nil {
		adminFailure(w, r, err)
		return
	}
	log := r.Context().Value(ctxKeyLog{}).(logrus.FieldLogger)
	if err := adminTemplates.ExecuteTemplate(w, "admin_dashboard", injectAdminData(r, map[string]interface{}{
		"active":             "dashboard",
		"title":              "Dashboard",
		"subtitle":           "Resumen de ventas confirmadas y actividad de la tienda.",
		"search_placeholder": "Search orders, products...",
		"dashboard":          dashboard,
	})); err != nil {
		log.Error(err)
	}
}

func (fe *frontendServer) adminOrdersHandler(w http.ResponseWriter, r *http.Request) {
	log := r.Context().Value(ctxKeyLog{}).(logrus.FieldLogger)
	page, _ := strconv.Atoi(r.URL.Query().Get("page"))
	orders, err := loadOrders(r, page)
	if err != nil {
		adminFailure(w, r, err)
		return
	}
	if err := adminTemplates.ExecuteTemplate(w, "admin_orders", injectAdminData(r, map[string]interface{}{
		"active":             "pedidos",
		"title":              "Pedidos",
		"subtitle":           "Gestión y seguimiento de órdenes recientes.",
		"search_placeholder": "Buscar pedidos...",
		"page":               orders,
	})); err != nil {
		log.Error(err)
	}
}

func (fe *frontendServer) adminCatalogHandler(w http.ResponseWriter, r *http.Request) {
	log := r.Context().Value(ctxKeyLog{}).(logrus.FieldLogger)
	page, _ := strconv.Atoi(r.URL.Query().Get("page"))
	products, err := loadProducts(r, page)
	if err != nil {
		adminFailure(w, r, err)
		return
	}
	if err := adminTemplates.ExecuteTemplate(w, "admin_catalog", injectAdminData(r, map[string]interface{}{
		"active":             "catalogo",
		"title":              "Gestión de Catálogo",
		"subtitle":           "Manage product listings, pricing, and availability.",
		"search_placeholder": "Search catalog...",
		"page":               products,
	})); err != nil {
		log.Error(err)
	}
}

func (fe *frontendServer) adminInventoryHandler(w http.ResponseWriter, r *http.Request) {
	log := r.Context().Value(ctxKeyLog{}).(logrus.FieldLogger)
	page, _ := strconv.Atoi(r.URL.Query().Get("page"))
	inventory, err := loadInventory(r, page)
	if err != nil {
		adminFailure(w, r, err)
		return
	}
	if err := adminTemplates.ExecuteTemplate(w, "admin_inventory", injectAdminData(r, map[string]interface{}{
		"active":             "inventario",
		"title":              "Control de Inventario",
		"subtitle":           "Manage stock levels across all warehouses.",
		"search_placeholder": "Search inventory...",
		"page":               inventory,
	})); err != nil {
		log.Error(err)
	}
}

func injectAdminData(r *http.Request, payload map[string]interface{}) map[string]interface{} {
	data := map[string]interface{}{
		"path":            r.URL.Path,
		"backend_enabled": adminBackendEnabled(),
		"csrf_token":      csrfForSession(adminSession(r)),
		"query":           r.URL.Query(),
		"baseUrl":         baseUrl,
		"request_id":      r.Context().Value(ctxKeyRequestID{}),
	}
	for k, v := range payload {
		data[k] = v
	}
	return data
}

// adminStatusClass maps a status label to a badge CSS class.
func adminStatusClass(status string) string {
	switch strings.ToLower(status) {
	case "entregado", "active", "in stock":
		return "st-ok"
	case "enviado", "shipped":
		return "st-info"
	case "pendiente", "pending", "low stock":
		return "st-warn"
	case "cancelado", "inactive", "out of stock":
		return "st-bad"
	}
	return "st-info"
}

func joinInts(ints []int, sep string) string {
	parts := make([]string, len(ints))
	for i, v := range ints {
		parts[i] = strconv.Itoa(v)
	}
	return strings.Join(parts, sep)
}
