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
// Admin panel — authentication (frontend-only phase)
//
// Single hardcoded admin account (overridable via ADMIN_EMAIL/ADMIN_PASSWORD)
// and in-memory sessions. This is intentionally simple: when a real backend
// exists, replace checkAdminCredentials + the session store with calls to an
// auth service / JWT validation. See docs/admin-panel.md.
// ---------------------------------------------------------------------------

import (
	"net/http"
	"os"
	"sync"
	"time"

	"github.com/google/uuid"
)

const (
	adminCookieName = "admin_session"
	adminSessionTTL = 24 * time.Hour
)

var (
	adminEmail    = envOrDefault("ADMIN_EMAIL", "admin@boutique.com")
	adminPassword = envOrDefault("ADMIN_PASSWORD", "admin123")

	adminSessions   = map[string]time.Time{}
	adminSessionsMu sync.Mutex
)

func envOrDefault(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}

func checkAdminCredentials(email, password string) bool {
	return email == adminEmail && password == adminPassword
}

func newAdminSession() string {
	token := uuid.NewString()
	adminSessionsMu.Lock()
	defer adminSessionsMu.Unlock()
	// Lazy cleanup of expired sessions.
	now := time.Now()
	for t, exp := range adminSessions {
		if now.After(exp) {
			delete(adminSessions, t)
		}
	}
	adminSessions[token] = now.Add(adminSessionTTL)
	return token
}

func destroyAdminSession(token string) {
	adminSessionsMu.Lock()
	defer adminSessionsMu.Unlock()
	delete(adminSessions, token)
}

func validAdminSession(r *http.Request) bool {
	c, err := r.Cookie(adminCookieName)
	if err != nil || c.Value == "" {
		return false
	}
	adminSessionsMu.Lock()
	defer adminSessionsMu.Unlock()
	exp, ok := adminSessions[c.Value]
	if !ok {
		return false
	}
	if time.Now().After(exp) {
		delete(adminSessions, c.Value)
		return false
	}
	return true
}

func setAdminSessionCookie(w http.ResponseWriter, token string) {
	http.SetCookie(w, &http.Cookie{
		Name:     adminCookieName,
		Value:    token,
		Path:     "/",
		MaxAge:   int(adminSessionTTL.Seconds()),
		HttpOnly: true,
		SameSite: http.SameSiteLaxMode,
		// TODO(backend): set Secure: true once served over HTTPS.
	})
}

func clearAdminSessionCookie(w http.ResponseWriter) {
	http.SetCookie(w, &http.Cookie{
		Name:     adminCookieName,
		Value:    "",
		Path:     "/",
		MaxAge:   -1,
		HttpOnly: true,
		SameSite: http.SameSiteLaxMode,
	})
}

// requireAdminAuth guards all /admin/* routes except /admin/login.
func requireAdminAuth(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if !validAdminSession(r) {
			http.Redirect(w, r, baseUrl+"/admin/login", http.StatusSeeOther)
			return
		}
		next.ServeHTTP(w, r)
	})
}
