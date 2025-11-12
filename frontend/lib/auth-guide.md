# Authentication Guide

## How Authentication Works

### Authentication Method: **Session-Based Authentication**

The application uses Django's built-in session framework for authentication. This is a secure, server-side authentication method.

---

## Authentication Flow

### 1. **User Sign Up**
```
┌─────────────┐
│   Frontend  │ User fills signup form
│  (Next.js)  │
└──────┬──────┘
       │ POST /api/auth/signup/
       │ { first_name, last_name, email, password, business_name? }
       ▼
┌─────────────┐
│   Backend   │ 1. Validates data
│   (Django)  │ 2. Creates User account
│             │ 3. Creates Business (if provided)
│             │ 4. Calls login(request, user)
│             │ 5. Creates session in database
│             │ 6. Sets sessionid cookie in response
└──────┬──────┘
       │ Response with Set-Cookie: sessionid=...
       ▼
┌─────────────┐
│   Browser   │ Stores sessionid cookie automatically
│             │ Cookie is HttpOnly (secure)
└──────┬──────┘
       │ Redirect to /dashboard
       ▼
┌─────────────┐
│  Dashboard  │ User is now authenticated
└─────────────┘
```

### 2. **User Sign In**
```
┌─────────────┐
│   Frontend  │ User enters email + password
│  (Next.js)  │
└──────┬──────┘
       │ POST /api/auth/login/
       │ { email, password }
       ▼
┌─────────────┐
│   Backend   │ 1. Validates credentials
│   (Django)  │ 2. Finds user by email
│             │ 3. Authenticates password
│             │ 4. Calls login(request, user)
│             │ 5. Creates/updates session
│             │ 6. Sets sessionid cookie
└──────┬──────┘
       │ Response with Set-Cookie: sessionid=...
       ▼
┌─────────────┐
│   Browser   │ Stores sessionid cookie
└──────┬──────┘
       │ Redirect to /dashboard
       ▼
┌─────────────┐
│  Dashboard  │ User authenticated
└─────────────┘
```

### 3. **Making Authenticated API Calls**
```
┌─────────────┐
│   Frontend  │ Makes API call (e.g., GET /api/auth/me/)
│  (Next.js)  │
└──────┬──────┘
       │ GET /api/auth/me/
       │ Cookie: sessionid=abc123... (sent automatically)
       ▼
┌─────────────┐
│   Backend   │ 1. SessionMiddleware reads sessionid cookie
│   (Django)  │ 2. Looks up session in database
│             │ 3. AuthenticationMiddleware attaches user to request
│             │ 4. DRF checks IsAuthenticated permission
│             │ 5. If authenticated → returns user data
│             │ 6. If not → returns 401 Unauthorized
└──────┬──────┘
       │ Response with user data
       ▼
┌─────────────┐
│   Frontend  │ Receives and displays data
└─────────────┘
```

### 4. **User Logout**
```
┌─────────────┐
│   Frontend  │ User clicks logout
│  (Next.js)  │
└──────┬──────┘
       │ POST /api/auth/logout/
       │ Cookie: sessionid=abc123...
       ▼
┌─────────────┐
│   Backend   │ 1. Verifies user is authenticated
│   (Django)  │ 2. Calls logout(request)
│             │ 3. Deletes session from database
│             │ 4. Clears sessionid cookie
└──────┬──────┘
       │ Response with cleared cookie
       ▼
┌─────────────┐
│   Browser   │ Removes sessionid cookie
└──────┬──────┘
       │ Redirect to /sign-in
       ▼
┌─────────────┐
│  Sign In    │ User must login again
└─────────────┘
```

---

## Technical Details

### Session Cookie
- **Name**: `sessionid`
- **Storage**: Server-side (database)
- **Cookie Properties**:
  - HttpOnly: ✅ Yes (prevents XSS attacks)
  - Secure: ✅ Yes (in production with HTTPS)
  - SameSite: Lax (CSRF protection)
  - Path: `/`
  - Expires: When browser closes (or configured timeout)

### Security Features

1. **CSRF Protection**
   - Django's CSRF middleware protects POST/PUT/DELETE requests
   - CSRF token required for state-changing operations
   - Configured via `CSRF_TRUSTED_ORIGINS`

2. **CORS Configuration**
   - Only allows requests from `http://localhost:3000`
   - Credentials (cookies) are included in requests
   - Configured via `CORS_ALLOWED_ORIGINS` and `CORS_ALLOW_CREDENTIALS`

3. **Session Security**
   - Sessions stored server-side in database
   - Only session ID travels in cookie (not user data)
   - HttpOnly flag prevents JavaScript access

### Frontend Implementation

**API Calls** (`frontend/lib/api.ts`):
```typescript
// All API calls include credentials
credentials: "include"  // Sends cookies automatically
```

**Authentication Endpoints**:
- `POST /api/auth/signup/` - Create account (AllowAny)
- `POST /api/auth/login/` - Login (AllowAny)
- `POST /api/auth/logout/` - Logout (IsAuthenticated)
- `GET /api/auth/me/` - Get current user (IsAuthenticated)

### Backend Implementation

**Authentication Classes**:
- `SessionAuthentication` - Uses Django sessions

**Permission Classes**:
- `IsAuthenticated` - Default for all API endpoints
- `AllowAny` - For signup/login endpoints

**Middleware Order** (important):
1. SecurityMiddleware
2. SessionMiddleware (creates session)
3. CorsMiddleware (handles CORS)
4. CommonMiddleware
5. CsrfViewMiddleware (CSRF protection)
6. AuthenticationMiddleware (attaches user to request)

---

## Advantages of Session-Based Auth

✅ **Secure**: Server-side sessions, HttpOnly cookies
✅ **Simple**: No token management needed
✅ **Automatic**: Browser handles cookies
✅ **CSRF Protected**: Built-in Django CSRF protection
✅ **Works with SSR**: Compatible with Next.js SSR/CSR

---

## Important Notes

1. **CSRF Tokens**: For POST/PUT/DELETE requests, Django may require CSRF tokens. Currently configured to trust frontend origin.

2. **Session Storage**: By default, Django stores sessions in the database. For production, consider Redis for better performance.

3. **Session Expiry**: Sessions expire when browser closes by default. Can be configured in Django settings.

4. **CORS**: Make sure `django-cors-headers` is installed and configured correctly.

5. **Environment Variables**: Frontend needs `NEXT_PUBLIC_API_URL` set to backend URL.

