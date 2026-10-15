package main

import (
	"context"
	"io"
	"net/http"
	"os"
	"strings"
	"time"

	pb "github.com/GoogleCloudPlatform/microservices-demo/src/productcatalogservice/genproto"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
	"google.golang.org/protobuf/encoding/protojson"
)

var catalogHTTP = &http.Client{Timeout: 3 * time.Second}

func readCatalogAPI(ctx context.Context) ([]*pb.Product, error) {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, strings.TrimRight(os.Getenv("CATALOG_API_BASE_URL"), "/")+"/internal/products", nil)
	if err != nil {
		return nil, status.Error(codes.Internal, "invalid catalog configuration")
	}
	req.Header.Set("Authorization", "Bearer "+os.Getenv("CATALOG_READ_TOKEN"))
	resp, err := catalogHTTP.Do(req)
	if err != nil {
		return nil, status.Error(codes.Unavailable, "catalog backend unavailable")
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return nil, status.Error(codes.Unavailable, "catalog backend not ready")
	}
	raw, err := io.ReadAll(io.LimitReader(resp.Body, 8<<20))
	if err != nil {
		return nil, status.Error(codes.Unavailable, "catalog response incomplete")
	}
	out := new(pb.ListProductsResponse)
	if err := protojson.Unmarshal(raw, out); err != nil {
		return nil, status.Error(codes.Internal, "catalog response invalid")
	}
	return out.Products, nil
}

func (p *productCatalog) products(ctx context.Context) ([]*pb.Product, error) {
	if os.Getenv("CATALOG_API_BASE_URL") != "" {
		return readCatalogAPI(ctx)
	}
	return p.parseCatalog(), nil
}
