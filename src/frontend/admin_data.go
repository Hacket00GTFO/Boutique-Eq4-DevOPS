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

// ---------------------------------------------------------------------------
// Admin panel — data layer
//
// AdminDataProvider is the seam between the admin panel frontend and a future
// backend. Handlers only talk to this interface; to wire real services,
// implement it on top of gRPC/REST clients and swap `adminData` in
// admin_handlers.go. See docs/admin-panel.md for the endpoint mapping.
// ---------------------------------------------------------------------------

type AdminStatCard struct {
	Label    string
	Value    string
	Icon     string // material symbol name shown top-right
	Delta    string // e.g. "+12.5%"
	DeltaUp  bool
	SubLabel string // alternative footer text (e.g. "Active Campaigns")
	// Sparkline points, normalized 0-100 (y axis, 0 = bottom).
	Spark []int
}

type AdminTopProduct struct {
	Name   string
	SKU    string
	Sales  int
	ImgURL string
}

type AdminOrder struct {
	ID            string
	Date          string
	Customer      string
	CustomerInit  string // avatar initials
	Total         string // pre-formatted amount, e.g. "€245.00"
	PaymentMethod string
	PaymentIcon   string // material symbol name, e.g. "credit_card"
	Status        string // ENTREGADO | ENVIADO | PENDIENTE | CANCELADO | Active | Shipped | Pending
}

type AdminProduct struct {
	Name     string
	Category string
	Price    string
	Status   string // Active | Inactive
	ImgURL   string
}

type AdminInventoryItem struct {
	SKU      string
	Product  string
	Variant  string
	Stock    int
	Status   string // In Stock | Low Stock | Out of Stock
	Location string
}

type AdminDashboardData struct {
	Stats        []AdminStatCard
	MonthlySales []int // 12 bars, normalized 0-100
	TopProducts  []AdminTopProduct
	RecentOrders []AdminOrder
}

type AdminOrdersPage struct {
	Orders      []AdminOrder
	DateRange   string // e.g. "01 Oct - 31 Oct"
	ShowingFrom int
	ShowingTo   int
	Total       int
}

type AdminCatalogPage struct {
	Products       []AdminProduct
	ActiveCategory string
	ShowingFrom    int
	ShowingTo      int
	Total          int
	CurrentPage    int
	TotalPages     int
}

type AdminInventoryPage struct {
	Stats        []AdminStatCard
	Items        []AdminInventoryItem
	ActiveFilter string
	ShowingFrom  int
	ShowingTo    int
	Total        int
}

// AdminDataProvider abstracts every data read the admin panel needs.
// Replace mockAdminData with an implementation backed by real services.
type AdminDataProvider interface {
	Dashboard() AdminDashboardData
	Orders(page int, dateRange string) AdminOrdersPage
	Products(page int, category string) AdminCatalogPage
	Inventory(page int, stockFilter string) AdminInventoryPage
}

// ---------------------------------------------------------------------------
// Mock implementation (frontend-only phase)
// ---------------------------------------------------------------------------

type mockAdminData struct{}

func (mockAdminData) Dashboard() AdminDashboardData {
	return AdminDashboardData{
		Stats: []AdminStatCard{
			{Label: "TOTAL REVENUE", Value: "$24,592.00", Icon: "payments", Delta: "+12.5%", DeltaUp: true,
				Spark: []int{20, 35, 30, 45, 40, 60, 75}},
			{Label: "TOTAL ORDERS", Value: "342", Icon: "assignment", Delta: "+5.2%", DeltaUp: true,
				Spark: []int{30, 25, 40, 35, 50, 45, 60}},
			{Label: "NEW CUSTOMERS", Value: "89", Icon: "group_add", Delta: "-2.1%", DeltaUp: false,
				Spark: []int{60, 50, 55, 40, 45, 30, 35}},
			{Label: "ACTIVE DISCOUNTS", Value: "12", Icon: "sell", SubLabel: "Active Campaigns"},
		},
		MonthlySales: []int{40, 65, 30, 85, 50, 70, 60, 90, 55, 75, 95, 100},
		TopProducts: []AdminTopProduct{
			{Name: "Merino Wool Sweater", SKU: "MW-092-GRY", Sales: 124, ImgURL: "/static/img/products/tank-top.jpg"},
			{Name: "Structured Leather Tote", SKU: "BG-LT-BLK", Sales: 98, ImgURL: "/static/img/products/loafers.jpg"},
			{Name: "Organic Cotton Classic T", SKU: "TS-OC-WHT", Sales: 85, ImgURL: "/static/img/products/tank-top.jpg"},
		},
		RecentOrders: []AdminOrder{
			{ID: "#ORD-9021", Date: "Oct 24, 10:42 AM", Customer: "Elena Herrera", CustomerInit: "EH", Total: "$340.00", Status: "Active"},
			{ID: "#ORD-9020", Date: "Oct 24, 09:15 AM", Customer: "Mateo Ramírez", CustomerInit: "MR", Total: "$125.50", Status: "Shipped"},
			{ID: "#ORD-9019", Date: "Oct 23, 16:30 PM", Customer: "Sofía López", CustomerInit: "SL", Total: "$890.00", Status: "Pending"},
		},
	}
}

func (m mockAdminData) Orders(page int, dateRange string) AdminOrdersPage {
	return AdminOrdersPage{
		Orders: []AdminOrder{
			{ID: "#ORD-9021", Date: "24 Oct, 2023", Customer: "Elena Rodríguez", Total: "€245.00",
				PaymentMethod: "Visa ending in 4242", PaymentIcon: "credit_card", Status: "ENTREGADO"},
			{ID: "#ORD-9020", Date: "23 Oct, 2023", Customer: "Mateo Silva", Total: "€1,120.50",
				PaymentMethod: "Transferencia", PaymentIcon: "account_balance", Status: "ENVIADO"},
			{ID: "#ORD-9019", Date: "23 Oct, 2023", Customer: "Sofía Costa", Total: "€89.90",
				PaymentMethod: "PayPal", PaymentIcon: "payments", Status: "PENDIENTE"},
			{ID: "#ORD-9018", Date: "22 Oct, 2023", Customer: "Lucas Martins", Total: "€350.00",
				PaymentMethod: "Mastercard ending 1123", PaymentIcon: "credit_card", Status: "CANCELADO"},
			{ID: "#ORD-9017", Date: "21 Oct, 2023", Customer: "Ana Santos", Total: "€125.00",
				PaymentMethod: "Amex ending 8008", PaymentIcon: "credit_card", Status: "ENTREGADO"},
		},
		DateRange:   "01 Oct - 31 Oct",
		ShowingFrom: 1,
		ShowingTo:   5,
		Total:       124,
	}
}

func (m mockAdminData) Products(page int, category string) AdminCatalogPage {
	all := []AdminProduct{
		{Name: "Oversized Linen Blazer", Category: "Women > Outerwear", Price: "$185.00", Status: "Active", ImgURL: "/static/img/products/tank-top.jpg"},
		{Name: "Classic Oxford Shirt", Category: "Men > Tops", Price: "$95.00", Status: "Active", ImgURL: "/static/img/products/tank-top.jpg"},
		{Name: "Merino Wool Beanie", Category: "Accessories > Headwear", Price: "$45.00", Status: "Inactive", ImgURL: "/static/img/products/sunglasses.jpg"},
		{Name: "Full-Grain Leather Wallet", Category: "Accessories > Wallets", Price: "$120.00", Status: "Active", ImgURL: "/static/img/products/watch.jpg"},
	}
	filtered := all[:0]
	for _, p := range all {
		if category == "" || category == "all" || matchAdminCategory(p.Category, category) {
			filtered = append(filtered, p)
		}
	}
	if category == "" {
		category = "all"
	}
	return AdminCatalogPage{
		Products:       filtered,
		ActiveCategory: category,
		ShowingFrom:    1,
		ShowingTo:      10,
		Total:          142,
		CurrentPage:    1,
		TotalPages:     15,
	}
}

func matchAdminCategory(cat, filter string) bool {
	switch filter {
	case "men":
		return len(cat) >= 3 && cat[:3] == "Men"
	case "women":
		return len(cat) >= 5 && cat[:5] == "Women"
	case "accessories":
		return len(cat) >= 11 && cat[:11] == "Accessories"
	}
	return true
}

func (m mockAdminData) Inventory(page int, stockFilter string) AdminInventoryPage {
	items := []AdminInventoryItem{
		{SKU: "APP-FW23-001", Product: "Merino Wool Overcoat", Variant: "L / Navy", Stock: 45, Status: "In Stock", Location: "NY Central"},
		{SKU: "APP-FW23-042", Product: "Cashmere Turtleneck", Variant: "M / Camel", Stock: 3, Status: "Low Stock", Location: "LA Hub"},
		{SKU: "APP-SS24-112", Product: "Pleated Wide Trousers", Variant: "32 / Charcoal", Stock: 128, Status: "In Stock", Location: "NY Central"},
		{SKU: "APP-SS24-008", Product: "Silk Camp Collar Shirt", Variant: "S / Ivory", Stock: 0, Status: "Out of Stock", Location: "NY Central"},
	}
	if stockFilter == "" {
		stockFilter = "all"
	}
	var filtered []AdminInventoryItem
	for _, it := range items {
		switch stockFilter {
		case "in":
			if it.Status == "In Stock" {
				filtered = append(filtered, it)
			}
		case "low":
			if it.Status == "Low Stock" {
				filtered = append(filtered, it)
			}
		case "out":
			if it.Status == "Out of Stock" {
				filtered = append(filtered, it)
			}
		default:
			filtered = append(filtered, it)
		}
	}
	return AdminInventoryPage{
		Stats: []AdminStatCard{
			{Label: "TOTAL SKUS", Value: "1,248", Icon: "inventory_2", Spark: []int{40, 55, 45, 60, 50, 70, 45}},
			{Label: "CRITICAL LOW STOCK", Value: "12 Items", Icon: "warning", Spark: []int{30, 60, 45, 55, 35, 70, 40}},
			{Label: "INCOMING SHIPMENTS", Value: "4 Expected", Icon: "local_shipping", Spark: []int{35, 45, 40, 55, 50, 65, 60}},
		},
		Items:        filtered,
		ActiveFilter: stockFilter,
		ShowingFrom:  1,
		ShowingTo:    4,
		Total:        1248,
	}
}
