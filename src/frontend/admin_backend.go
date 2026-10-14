package main

import (
	"bytes"
	"context"
	"crypto/hmac"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"strings"
	"time"
)

type adminAPIError struct {
	Status  int
	Message string
}

func (e *adminAPIError) Error() string { return e.Message }

var adminHTTP = &http.Client{Timeout: 5 * time.Second}

func adminBackendEnabled() bool { return os.Getenv("ADMIN_DATA_MODE") == "backend" }
func adminSession(r *http.Request) string {
	c, err := r.Cookie(adminCookieName)
	if err != nil {
		return ""
	}
	return c.Value
}

func adminAPI(ctx context.Context, method, path, session string, data, out any) error {
	var body io.Reader
	if data != nil {
		raw, err := json.Marshal(data)
		if err != nil {
			return err
		}
		body = bytes.NewReader(raw)
	}
	req, err := http.NewRequestWithContext(ctx, method, strings.TrimRight(os.Getenv("ADMIN_API_BASE_URL"), "/")+path, body)
	if err != nil {
		return err
	}
	req.Header.Set("Authorization", "Bearer "+os.Getenv("ADMIN_API_TOKEN"))
	req.Header.Set("X-Admin-Session", session)
	req.Header.Set("Content-Type", "application/json")
	res, err := adminHTTP.Do(req)
	if err != nil {
		return &adminAPIError{503, "Backend administrativo temporalmente no disponible"}
	}
	defer res.Body.Close()
	raw, err := io.ReadAll(io.LimitReader(res.Body, 8<<20))
	if err != nil {
		return err
	}
	if res.StatusCode >= 400 {
		var detail struct {
			Message string `json:"message"`
		}
		_ = json.Unmarshal(raw, &detail)
		if detail.Message == "" {
			detail.Message = "No se pudo completar la operación"
		}
		return &adminAPIError{res.StatusCode, detail.Message}
	}
	if out != nil {
		return json.Unmarshal(raw, out)
	}
	return nil
}

func adminFailure(w http.ResponseWriter, r *http.Request, err error) {
	code := http.StatusServiceUnavailable
	if e, ok := err.(*adminAPIError); ok {
		code = e.Status
	}
	if code == 401 {
		clearAdminSessionCookie(w)
		http.Redirect(w, r, baseUrl+"/admin/login", http.StatusSeeOther)
		return
	}
	http.Error(w, err.Error(), code)
}

func csrfForSession(token string) string {
	mac := hmac.New(sha256.New, []byte(os.Getenv("ADMIN_API_TOKEN")))
	mac.Write([]byte("admin-csrf:" + token))
	return hex.EncodeToString(mac.Sum(nil))
}

func requireAdminCSRF(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodGet && r.Method != http.MethodHead {
			r.Body = http.MaxBytesReader(w, r.Body, 1<<20)
			if !hmac.Equal([]byte(r.FormValue("csrf_token")), []byte(csrfForSession(adminSession(r)))) {
				http.Error(w, "Token CSRF inválido", http.StatusForbidden)
				return
			}
		}
		next.ServeHTTP(w, r)
	})
}

func adminQuery(r *http.Request) string { return "?" + r.URL.Query().Encode() }
func loadDashboard(r *http.Request) (AdminDashboardData, error) {
	if !adminBackendEnabled() {
		return adminData.Dashboard(), nil
	}
	var out AdminDashboardData
	err := adminAPI(r.Context(), "GET", "/api/v1/dashboard"+adminQuery(r), adminSession(r), nil, &out)
	return out, err
}
func loadOrders(r *http.Request, page int) (AdminOrdersPage, error) {
	if !adminBackendEnabled() {
		return adminData.Orders(page, r.URL.Query().Get("range")), nil
	}
	var out AdminOrdersPage
	err := adminAPI(r.Context(), "GET", "/api/v1/orders"+adminQuery(r), adminSession(r), nil, &out)
	return out, err
}
func loadProducts(r *http.Request, page int) (AdminCatalogPage, error) {
	if !adminBackendEnabled() {
		return adminData.Products(page, r.URL.Query().Get("category")), nil
	}
	var out AdminCatalogPage
	err := adminAPI(r.Context(), "GET", "/api/v1/products"+adminQuery(r), adminSession(r), nil, &out)
	return out, err
}
func loadInventory(r *http.Request, page int) (AdminInventoryPage, error) {
	if !adminBackendEnabled() {
		return adminData.Inventory(page, r.URL.Query().Get("stock")), nil
	}
	var out AdminInventoryPage
	err := adminAPI(r.Context(), "GET", "/api/v1/inventory"+adminQuery(r), adminSession(r), nil, &out)
	return out, err
}

func (fe *frontendServer) adminProductForm(w http.ResponseWriter, r *http.Request) {
	p := AdminProduct{Status: "Active", PriceValue: "0", Category: "accessories", Variant: "Standard", Location: "Principal"}
	id := r.URL.Query().Get("id")
	if id != "" {
		if err := adminAPI(r.Context(), "GET", "/api/v1/products/"+url.PathEscape(id), adminSession(r), nil, &p); err != nil {
			adminFailure(w, r, err)
			return
		}
	}
	if err := adminTemplates.ExecuteTemplate(w, "admin_product_form", injectAdminData(r, map[string]interface{}{"title": "Producto", "active": "catalogo", "product": p})); err != nil {
		http.Error(w, "Error de plantilla", 500)
	}
}

func (fe *frontendServer) adminSaveProduct(w http.ResponseWriter, r *http.Request) {
	data := map[string]any{"name": r.FormValue("name"), "sku": r.FormValue("sku"), "description": r.FormValue("description"), "picture": r.FormValue("picture"), "price": r.FormValue("price"), "category": r.FormValue("category"), "active": r.FormValue("active") == "true", "variant": r.FormValue("variant"), "warehouse": r.FormValue("warehouse"), "version": r.FormValue("version")}
	method, path := "POST", "/api/v1/products"
	if id := r.FormValue("id"); id != "" {
		method, path = "PATCH", path+"/"+url.PathEscape(id)
	}
	if err := adminAPI(r.Context(), method, path, adminSession(r), data, nil); err != nil {
		adminFailure(w, r, err)
		return
	}
	http.Redirect(w, r, baseUrl+"/admin/catalogo", http.StatusSeeOther)
}

func (fe *frontendServer) adminArchiveProduct(w http.ResponseWriter, r *http.Request) {
	if err := adminAPI(r.Context(), "DELETE", "/api/v1/products/"+url.PathEscape(r.FormValue("id")), adminSession(r), map[string]string{"version": r.FormValue("version")}, nil); err != nil {
		adminFailure(w, r, err)
		return
	}
	http.Redirect(w, r, baseUrl+"/admin/catalogo", http.StatusSeeOther)
}

func (fe *frontendServer) adminAdjustStock(w http.ResponseWriter, r *http.Request) {
	data := map[string]string{"product_id": r.FormValue("product_id"), "delta": r.FormValue("delta"), "reason": r.FormValue("reason")}
	if err := adminAPI(r.Context(), "POST", "/api/v1/inventory/adjustments", adminSession(r), data, nil); err != nil {
		adminFailure(w, r, err)
		return
	}
	http.Redirect(w, r, baseUrl+"/admin/inventario", http.StatusSeeOther)
}

func (fe *frontendServer) adminOrderDetail(w http.ResponseWriter, r *http.Request) {
	var detail struct {
		AdminOrder
		Items []struct {
			Name, SKU, Price string
			Quantity         int
		}
	}
	if err := adminAPI(r.Context(), "GET", "/api/v1/orders/"+url.PathEscape(r.URL.Query().Get("id")), adminSession(r), nil, &detail); err != nil {
		adminFailure(w, r, err)
		return
	}
	if err := adminTemplates.ExecuteTemplate(w, "admin_order_detail", injectAdminData(r, map[string]interface{}{"title": "Detalle de pedido", "active": "pedidos", "order": detail})); err != nil {
		http.Error(w, "Error de plantilla", 500)
	}
}

func (fe *frontendServer) adminOrderStatus(w http.ResponseWriter, r *http.Request) {
	if err := adminAPI(r.Context(), "PATCH", "/api/v1/orders/"+url.PathEscape(r.FormValue("id"))+"/status", adminSession(r), map[string]string{"status": r.FormValue("status")}, nil); err != nil {
		adminFailure(w, r, err)
		return
	}
	http.Redirect(w, r, baseUrl+"/admin/pedidos", http.StatusSeeOther)
}

func (fe *frontendServer) adminReconcileOrder(w http.ResponseWriter, r *http.Request) {
	data := map[string]any{"state": r.FormValue("state"), "transaction_id": r.FormValue("transaction_id"), "reason": r.FormValue("reason"), "verified": r.FormValue("verified") == "true"}
	if err := adminAPI(r.Context(), "POST", "/api/v1/orders/"+url.PathEscape(r.FormValue("id"))+"/reconcile", adminSession(r), data, nil); err != nil {
		adminFailure(w, r, err)
		return
	}
	http.Redirect(w, r, baseUrl+"/admin/pedidos/detail?id="+url.QueryEscape(r.FormValue("id")), http.StatusSeeOther)
}

func (fe *frontendServer) adminExport(w http.ResponseWriter, r *http.Request) {
	resource := r.URL.Query().Get("resource")
	if resource != "products" && resource != "inventory" && resource != "orders" {
		http.NotFound(w, r)
		return
	}
	req, err := http.NewRequestWithContext(r.Context(), "GET", strings.TrimRight(os.Getenv("ADMIN_API_BASE_URL"), "/")+"/api/v1/exports/"+resource+adminQuery(r), nil)
	if err != nil {
		adminFailure(w, r, err)
		return
	}
	req.Header.Set("Authorization", "Bearer "+os.Getenv("ADMIN_API_TOKEN"))
	req.Header.Set("X-Admin-Session", adminSession(r))
	res, err := adminHTTP.Do(req)
	if err != nil {
		adminFailure(w, r, err)
		return
	}
	defer res.Body.Close()
	if res.StatusCode != 200 {
		adminFailure(w, r, &adminAPIError{res.StatusCode, "No se pudo exportar"})
		return
	}
	w.Header().Set("Content-Type", "text/csv; charset=utf-8")
	w.Header().Set("Content-Disposition", fmt.Sprintf("attachment; filename=%q", resource+".csv"))
	_, _ = io.Copy(w, io.LimitReader(res.Body, 8<<20))
}
