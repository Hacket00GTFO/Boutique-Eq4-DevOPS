package main

import (
	"github.com/google/uuid"
	"net/http"
	"net/url"
)

func (fe *frontendServer) adminDemoSeed(w http.ResponseWriter, r *http.Request) {
	if err := adminAPI(r.Context(), "POST", "/api/v1/demo/seed", adminSession(r), map[string]string{}, nil); err != nil {
		adminFailure(w, r, err)
		return
	}
	http.Redirect(w, r, baseUrl+"/admin?notice=demo-loaded", http.StatusSeeOther)
}

func (fe *frontendServer) adminDemoOrderForm(w http.ResponseWriter, r *http.Request) {
	var options struct {
		Products []struct {
			AdminProduct
			Available int
		}
	}
	if err := adminAPI(r.Context(), "GET", "/api/v1/demo/products", adminSession(r), nil, &options); err != nil {
		adminFailure(w, r, err)
		return
	}
	if err := adminTemplates.ExecuteTemplate(w, "admin_order_form", injectAdminData(r, map[string]interface{}{
		"title": "Crear pedido demo", "active": "pedidos", "products": options.Products, "order_key": uuid.NewString(),
	})); err != nil {
		http.Error(w, "Error de plantilla", 500)
	}
}

func (fe *frontendServer) adminDemoOrderCreate(w http.ResponseWriter, r *http.Request) {
	items := []map[string]string{}
	ids, quantities := r.Form["product_id"], r.Form["quantity"]
	if len(ids) != len(quantities) || len(ids) > 20 {
		adminFailure(w, r, &adminAPIError{422, "Productos o cantidades inválidos"})
		return
	}
	for i, id := range ids {
		if id != "" {
			items = append(items, map[string]string{"product_id": id, "quantity": quantities[i]})
		}
	}
	data := map[string]any{
		"key": r.FormValue("order_key"), "email": r.FormValue("email"), "customer_name": r.FormValue("customer_name"),
		"shipping_fee": r.FormValue("shipping_fee"), "items": items,
		"address": map[string]string{"streetAddress": r.FormValue("street_address"), "city": r.FormValue("city"),
			"state": r.FormValue("state"), "country": r.FormValue("country"), "zipCode": r.FormValue("zip_code")},
	}
	var order AdminOrder
	if err := adminAPI(r.Context(), "POST", "/api/v1/demo/orders", adminSession(r), data, &order); err != nil {
		adminFailure(w, r, err)
		return
	}
	http.Redirect(w, r, baseUrl+"/admin/pedidos/detail?id="+url.QueryEscape(order.ID)+"&notice=saved", http.StatusSeeOther)
}

func (fe *frontendServer) adminShipmentSave(w http.ResponseWriter, r *http.Request) {
	data := map[string]string{"version": r.FormValue("version"), "status": r.FormValue("status"),
		"carrier": r.FormValue("carrier"), "tracking_id": r.FormValue("tracking_id"),
		"estimated_delivery": r.FormValue("estimated_delivery"), "notes": r.FormValue("notes")}
	if err := adminAPI(r.Context(), "PATCH", "/api/v1/orders/"+url.PathEscape(r.FormValue("id"))+"/shipment", adminSession(r), data, nil); err != nil {
		adminFailure(w, r, err)
		return
	}
	http.Redirect(w, r, baseUrl+"/admin/pedidos/detail?id="+url.QueryEscape(r.FormValue("id"))+"&notice=saved", http.StatusSeeOther)
}

func (fe *frontendServer) adminDemoOrderCancel(w http.ResponseWriter, r *http.Request) {
	if err := adminAPI(r.Context(), "POST", "/api/v1/orders/"+url.PathEscape(r.FormValue("id"))+"/cancel-demo", adminSession(r), map[string]string{"reason": r.FormValue("reason")}, nil); err != nil {
		adminFailure(w, r, err)
		return
	}
	http.Redirect(w, r, baseUrl+"/admin/pedidos/detail?id="+url.QueryEscape(r.FormValue("id"))+"&notice=cancelled", http.StatusSeeOther)
}

func (fe *frontendServer) adminActivateProduct(w http.ResponseWriter, r *http.Request) {
	if err := adminAPI(r.Context(), "PATCH", "/api/v1/products/"+url.PathEscape(r.FormValue("id"))+"/activation", adminSession(r),
		map[string]any{"version": r.FormValue("version"), "active": true}, nil); err != nil {
		adminFailure(w, r, err)
		return
	}
	http.Redirect(w, r, baseUrl+"/admin/catalogo?notice=saved", http.StatusSeeOther)
}

func (fe *frontendServer) adminInventoryDetail(w http.ResponseWriter, r *http.Request) {
	var detail struct {
		Item      AdminInventoryItem
		Movements []struct {
			Delta               int
			Reason, Actor, Date string
		}
	}
	if err := adminAPI(r.Context(), "GET", "/api/v1/inventory/"+url.PathEscape(r.URL.Query().Get("id")), adminSession(r), nil, &detail); err != nil {
		adminFailure(w, r, err)
		return
	}
	if err := adminTemplates.ExecuteTemplate(w, "admin_inventory_detail", injectAdminData(r, map[string]interface{}{
		"title": "Detalle de inventario", "active": "inventario", "inventory": detail,
	})); err != nil {
		http.Error(w, "Error de plantilla", 500)
	}
}
