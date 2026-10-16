package main

import (
	"context"
	"google.golang.org/grpc"
	healthpb "google.golang.org/grpc/health/grpc_health_v1"
	"net/http"
	"os"
	"strings"
	"time"
)

func boundedRPC(ctx context.Context, method string, req, reply any, cc *grpc.ClientConn, invoke grpc.UnaryInvoker, opts ...grpc.CallOption) error {
	timeout := 3 * time.Second
	if strings.Contains(method, "CheckoutService/") {
		timeout = 20 * time.Second
	}
	ctx, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()
	return invoke(ctx, method, req, reply, cc, opts...)
}

func (fe *frontendServer) readyHandler(w http.ResponseWriter, r *http.Request) {
	ctx, cancel := context.WithTimeout(r.Context(), 4*time.Second)
	defer cancel()
	for _, conn := range []*grpc.ClientConn{fe.productCatalogSvcConn, fe.cartSvcConn, fe.currencySvcConn, fe.checkoutSvcConn} {
		health, err := healthpb.NewHealthClient(conn).Check(ctx, &healthpb.HealthCheckRequest{})
		if err != nil || health.Status != healthpb.HealthCheckResponse_SERVING {
			http.Error(w, "critical dependency unavailable", 503)
			return
		}
	}
	if adminBackendEnabled() {
		req, err := http.NewRequestWithContext(ctx, "GET", strings.TrimRight(os.Getenv("ADMIN_API_BASE_URL"), "/")+"/readyz", nil)
		if err != nil {
			http.Error(w, "admin configuration invalid", 503)
			return
		}
		res, err := adminHTTP.Do(req)
		if err != nil {
			http.Error(w, "admin unavailable", 503)
			return
		}
		defer res.Body.Close()
		if res.StatusCode != 200 {
			http.Error(w, "database unavailable", 503)
			return
		}
	}
	w.Write([]byte("ready"))
}
