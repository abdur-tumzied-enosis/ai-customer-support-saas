# Authentication Flow

## Overview
The application uses **Session-Based Authentication** with Django's built-in session framework.

## How It Works

### 1. **Sign Up Flow**
```
Frontend (Sign Up Page)
  ↓
POST /api/auth/signup/
  ↓
Backend creates User + Business (optional)
  ↓
Backend calls login(request, user) → Creates session
  ↓
Backend sets session cookie in response
  ↓
Browser stores session cookie (sessionid)
  ↓
Frontend redirects to /dashboard
```

### 2. **Sign In Flow**
```
Frontend (Sign In Page)
  ↓
POST /api/auth/login/ with email + password
  ↓
Backend validates credentials
  ↓
Backend calls login(request, user) → Creates session
  ↓
Backend sets session cookie in response
  ↓
Browser stores session cookie (sessionid)
  ↓
Frontend redirects to /dashboard
```

### 3. **Authenticated Requests**
```
Frontend makes API call
  ↓
Browser automatically includes sessionid cookie
  ↓
Backend receives request with session cookie
  ↓
Django SessionMiddleware reads session
  ↓
AuthenticationMiddleware attaches user to request
  ↓
DRF checks IsAuthenticated permission
  ↓
Request proceeds if authenticated
```

### 4. **Logout Flow**
```
Frontend calls POST /api/auth/logout/
  ↓
Backend calls logout(request) → Destroys session
  ↓
Backend clears session cookie
  ↓
Frontend redirects to /sign-in
```

## Session Cookie Details

- **Name**: `sessionid`
- **HttpOnly**: Yes (set by Django, prevents XSS)
- **Secure**: In production (HTTPS only)
- **SameSite**: Lax (CSRF protection)
- **Path**: `/`
- **Expires**: When browser closes (or configured timeout)

## Security Features

1. **CSRF Protection**: Django's CSRF middleware protects against CSRF attacks
2. **Session Security**: Sessions stored server-side, only session ID in cookie
3. **HttpOnly Cookies**: Prevents JavaScript access to session cookie
4. **CORS**: Configured to allow credentials from frontend origin only

## Current Implementation

- ✅ Session-based authentication
- ✅ Automatic cookie handling (credentials: "include")
- ✅ Protected API endpoints (IsAuthenticated permission)
- ✅ CORS configured for credentials
- ✅ CSRF protection enabled

## Notes

- Sessions are stored in the database by default (Django's default)
- Session data is server-side, only session ID travels in cookie
- No tokens needed - browser handles cookies automatically
- Works seamlessly with Next.js SSR/CSR

