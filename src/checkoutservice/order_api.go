package main

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"strings"
	"time"

	pb "github.com/GoogleCloudPlatform/microservices-demo/src/checkoutservice/genproto"
	"github.com/GoogleCloudPlatform/microservices-demo/src/checkoutservice/money"
	"github.com/google/uuid"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/metadata"
	"google.golang.org/grpc/status"
	"google.golang.org/protobuf/encoding/protojson"
	"google.golang.org/protobuf/proto"
)

type durableOrder struct {
	ID     string          `json:"id"`
	State  string          `json:"state"`
	Result json.RawMessage `json:"result"`
	Email  string          `json:"email"`
}

var orderHTTP = &http.Client{Timeout: 5 * time.Second}

func orderAPI(ctx context.Context, method, path string, data, out any) error {
	var body io.Reader
	if data != nil {
		raw, err := json.Marshal(data)
		if err != nil {
			return err
		}
		body = bytes.NewReader(raw)
	}
	req, err := http.NewRequestWithContext(ctx, method, strings.TrimRight(os.Getenv("ORDER_API_BASE_URL"), "/")+path, body)
	if err != nil {
		return err
	}
	req.Header.Set("Authorization", "Bearer "+os.Getenv("CHECKOUT_API_TOKEN"))
	req.Header.Set("Content-Type", "application/json")
	res, err := orderHTTP.Do(req)
	if err != nil {
		return status.Error(codes.Unavailable, "order backend unavailable")
	}
	defer res.Body.Close()
	raw, err := io.ReadAll(io.LimitReader(res.Body, 1<<20))
	if err != nil {
		return err
	}
	if res.StatusCode >= 400 {
		code := codes.Unavailable
		if res.StatusCode == 404 {
			code = codes.NotFound
		}
		if res.StatusCode == 409 {
			code = codes.Aborted
		}
		if res.StatusCode == 422 {
			code = codes.InvalidArgument
		}
		var detail struct {
			Message string `json:"message"`
		}
		_ = json.Unmarshal(raw, &detail)
		return status.Error(code, detail.Message)
	}
	if out != nil {
		return json.Unmarshal(raw, out)
	}
	return nil
}
func protoJSON(message proto.Message) json.RawMessage {
	raw, _ := protojson.Marshal(message)
	return raw
}
func moneySum(total, price pb.Money, quantity int32) pb.Money {
	return money.Must(money.Sum(total, money.MultiplySlow(price, uint32(quantity))))
}

func (cs *checkoutService) durablePlaceOrder(ctx context.Context, req *pb.PlaceOrderRequest) (*pb.PlaceOrderResponse, error) {
	ctx, cancel := context.WithTimeout(ctx, 15*time.Second)
	defer cancel()
	key := uuid.NewString()
	if md, ok := metadata.FromIncomingContext(ctx); ok {
		if keys := md.Get("idempotency-key"); len(keys) > 0 {
			key = keys[0]
		}
	}
	if len(key) > 128 || key == "" || req.UserId == "" {
		return nil, status.Error(codes.InvalidArgument, "invalid checkout key or user")
	}
	var order durableOrder
	err := orderAPI(ctx, "GET", "/internal/orders/by-key?user_id="+url.QueryEscape(req.UserId)+"&key="+url.QueryEscape(key), nil, &order)
	if status.Code(err) == codes.NotFound {
		cart, err := cs.getUserCart(ctx, req.UserId)
		if err != nil {
			return nil, err
		}
		if len(cart) == 0 {
			return nil, status.Error(codes.InvalidArgument, "empty cart")
		}
		items := make([]map[string]any, 0, len(cart))
		for _, item := range cart {
			if item.Quantity < 1 || item.Quantity > 10 {
				return nil, status.Error(codes.InvalidArgument, "invalid cart quantity")
			}
			product, err := pb.NewProductCatalogServiceClient(cs.productCatalogSvcConn).GetProduct(ctx, &pb.GetProductRequest{Id: item.ProductId})
			if err != nil {
				return nil, err
			}
			cost, err := cs.convertCurrency(ctx, product.PriceUsd, req.UserCurrency)
			if err != nil {
				return nil, err
			}
			items = append(items, map[string]any{"item": protoJSON(item), "cost": protoJSON(cost), "price_usd": protoJSON(product.PriceUsd)})
		}
		shippingUSD, err := cs.quoteShipping(ctx, req.Address, cart)
		if err != nil {
			return nil, err
		}
		shipping, err := cs.convertCurrency(ctx, shippingUSD, req.UserCurrency)
		if err != nil {
			return nil, err
		}
		lastFour := req.GetCreditCard().GetCreditCardNumber()
		if len(lastFour) > 4 {
			lastFour = lastFour[len(lastFour)-4:]
		}
		data := map[string]any{"key": key, "user_id": req.UserId, "email": req.Email, "currency": req.UserCurrency, "items": items, "shipping_cost": protoJSON(shipping), "shipping_cost_usd": protoJSON(shippingUSD), "address": protoJSON(req.Address), "last_four": lastFour}
		if err := orderAPI(ctx, "POST", "/internal/orders", data, &order); err != nil {
			return nil, err
		}
	} else if err != nil {
		return nil, err
	}
	result := new(pb.OrderResult)
	if err := protojson.Unmarshal(order.Result, result); err != nil {
		return nil, status.Error(codes.Internal, "invalid persisted order")
	}
	if order.Email != req.Email || result.GetShippingCost().GetCurrencyCode() != req.UserCurrency || !proto.Equal(result.ShippingAddress, req.Address) {
		return nil, status.Error(codes.Aborted, "checkout key belongs to a different request")
	}
	switch order.State {
	case "confirmed":
		return &pb.PlaceOrderResponse{Order: result}, nil
	case "paid": // Resume shipping/confirmation without a second charge.
	case "reserved":
		if err := orderAPI(ctx, "POST", "/internal/orders/"+order.ID+"/claim", map[string]string{}, nil); err != nil {
			return nil, err
		}
		total := *result.ShippingCost
		for _, item := range result.Items {
			total = moneySum(total, *item.Cost, item.Item.Quantity)
		}
		paymentCtx := metadata.AppendToOutgoingContext(ctx, "idempotency-key", order.ID)
		tx, err := cs.chargeCard(paymentCtx, &total, req.CreditCard)
		if err != nil {
			next := "payment_unknown"
			if status.Code(err) == codes.InvalidArgument {
				next = "failed"
			}
			recovery, recoveryCancel := context.WithTimeout(context.Background(), 5*time.Second)
			_ = orderAPI(recovery, "POST", "/internal/orders/"+order.ID+"/payment", map[string]string{"state": next}, nil)
			recoveryCancel()
			return nil, status.Error(status.Code(err), "payment failed; order status persisted for reconciliation")
		}
		if err := orderAPI(ctx, "POST", "/internal/orders/"+order.ID+"/payment", map[string]string{"state": "paid", "transaction_id": tx}, nil); err != nil {
			return nil, err
		}
	default:
		return nil, status.Error(codes.Aborted, "order requires reconciliation; no repeated charge")
	}
	items := make([]*pb.CartItem, 0, len(result.Items))
	for _, i := range result.Items {
		items = append(items, i.Item)
	}
	tracking, err := cs.shipOrder(metadata.AppendToOutgoingContext(ctx, "idempotency-key", order.ID), result.ShippingAddress, items)
	if err != nil {
		return nil, err
	}
	if err := orderAPI(ctx, "POST", "/internal/orders/"+order.ID+"/confirm", map[string]string{"tracking_id": tracking}, &order); err != nil {
		return nil, err
	}
	if err := protojson.Unmarshal(order.Result, result); err != nil {
		return nil, err
	}
	if err := cs.emptyUserCart(ctx, req.UserId); err != nil {
		log.Warn("confirmed order; cart cleanup failed")
	}
	return &pb.PlaceOrderResponse{Order: result}, nil
}

// Email delivery is retried from the durable orders table; never blocks checkout.
func (cs *checkoutService) runEmailWorker() {
	ticker := time.NewTicker(10 * time.Second)
	defer ticker.Stop()
	for range ticker.C {
		ctx, cancel := context.WithTimeout(context.Background(), 8*time.Second)
		var jobs struct {
			Jobs []durableOrder `json:"jobs"`
		}
		if err := orderAPI(ctx, "GET", "/internal/email-jobs", nil, &jobs); err != nil {
			log.Warn("email jobs unavailable")
			cancel()
			continue
		}
		for _, job := range jobs.Jobs {
			result := new(pb.OrderResult)
			if err := protojson.Unmarshal(job.Result, result); err != nil {
				continue
			}
			if err := cs.sendOrderConfirmation(ctx, job.Email, result); err == nil {
				_ = orderAPI(ctx, "POST", fmt.Sprintf("/internal/orders/%s/email-sent", job.ID), map[string]string{}, nil)
			}
		}
		cancel()
	}
}
