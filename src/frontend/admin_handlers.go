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
	"html/template"
	"net/http"
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
		}).ParseGlob("templates/admin/*.html"))
)

func (fe *frontendServer) adminLoginViewHandler(w http.ResponseWriter, r *http.Request) {
	log := r.Context().Value(ctxKeyLog{}).(logrus.FieldLogger)
	if validAdminSession(r) {
		http.Redirect(w, r, baseUrl+"/admin", http.StatusSeeOther)
		return
	}
	if err := adminTemplates.ExecuteTemplate(w, "admin_login", injectAdminData(r, map[string]interface{}{
		"login_email": "",
	})); err != nil {
		log.Error(err)
	}
}

func (fe *frontendServer) adminLoginSubmitHandler(w http.ResponseWriter, r *http.Request) {
	log := r.Context().Value(ctxKeyLog{}).(logrus.FieldLogger)
	email := strings.TrimSpace(r.FormValue("email"))
	password := r.FormValue("password")

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
	log := r.Context().Value(ctxKeyLog{}).(logrus.FieldLogger)
	if c, err := r.Cookie(adminCookieName); err == nil {
		destroyAdminSession(c.Value)
	}
	clearAdminSessionCookie(w)
	log.Debug("admin logged out")
	http.Redirect(w, r, baseUrl+"/admin/login", http.StatusSeeOther)
}

func (fe *frontendServer) adminDashboardHandler(w http.ResponseWriter, r *http.Request) {
	log := r.Context().Value(ctxKeyLog{}).(logrus.FieldLogger)
	if err := adminTemplates.ExecuteTemplate(w, "admin_dashboard", injectAdminData(r, map[string]interface{}{
		"active":             "dashboard",
		"title":              "Dashboard",
		"subtitle":           "Overview of your store's performance today.",
		"search_placeholder": "Search orders, products...",
		"dashboard":          adminData.Dashboard(),
	})); err != nil {
		log.Error(err)
	}
}

func (fe *frontendServer) adminOrdersHandler(w http.ResponseWriter, r *http.Request) {
	log := r.Context().Value(ctxKeyLog{}).(logrus.FieldLogger)
	page, _ := strconv.Atoi(r.URL.Query().Get("page"))
	if err := adminTemplates.ExecuteTemplate(w, "admin_orders", injectAdminData(r, map[string]interface{}{
		"active":             "pedidos",
		"title":              "Pedidos",
		"subtitle":           "Gestión y seguimiento de órdenes recientes.",
		"search_placeholder": "Buscar pedidos...",
		"page":               adminData.Orders(page, r.URL.Query().Get("range")),
	})); err != nil {
		log.Error(err)
	}
}

func (fe *frontendServer) adminCatalogHandler(w http.ResponseWriter, r *http.Request) {
	log := r.Context().Value(ctxKeyLog{}).(logrus.FieldLogger)
	page, _ := strconv.Atoi(r.URL.Query().Get("page"))
	if err := adminTemplates.ExecuteTemplate(w, "admin_catalog", injectAdminData(r, map[string]interface{}{
		"active":             "catalogo",
		"title":              "Gestión de Catálogo",
		"subtitle":           "Manage product listings, pricing, and availability.",
		"search_placeholder": "Search catalog...",
		"page":               adminData.Products(page, r.URL.Query().Get("category")),
	})); err != nil {
		log.Error(err)
	}
}

func (fe *frontendServer) adminInventoryHandler(w http.ResponseWriter, r *http.Request) {
	log := r.Context().Value(ctxKeyLog{}).(logrus.FieldLogger)
	page, _ := strconv.Atoi(r.URL.Query().Get("page"))
	if err := adminTemplates.ExecuteTemplate(w, "admin_inventory", injectAdminData(r, map[string]interface{}{
		"active":             "inventario",
		"title":              "Control de Inventario",
		"subtitle":           "Manage stock levels across all warehouses.",
		"search_placeholder": "Search inventory...",
		"page":               adminData.Inventory(page, r.URL.Query().Get("stock")),
	})); err != nil {
		log.Error(err)
	}
}

func injectAdminData(r *http.Request, payload map[string]interface{}) map[string]interface{} {
	data := map[string]interface{}{
		"baseUrl":    baseUrl,
		"request_id": r.Context().Value(ctxKeyRequestID{}),
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
