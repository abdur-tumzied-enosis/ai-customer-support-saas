# ---- Base image ----
FROM node:20-alpine AS base
ENV NEXT_TELEMETRY_DISABLED=1
WORKDIR /app

# ---- Dependencies ----
FROM base AS deps
COPY package.json package-lock.json* ./
RUN npm ci --no-audit --no-fund

# ---- Builder ----
FROM base AS builder
COPY --from=deps /app/node_modules ./node_modules
COPY . .
# Build Next.js (standalone output configured in next.config.mjs)
RUN npm run build

# ---- Runner ----
FROM node:20-alpine AS runner
ENV NODE_ENV=production
ENV PORT=3000
WORKDIR /app

# Create non-root user
RUN addgroup -g 1001 -S nodejs && adduser -S nextjs -u 1001

# Copy standalone server and static assets
COPY --from=builder /app/.next/standalone ./ 
COPY --from=builder /app/.next/static ./.next/static
COPY --from=builder /app/public ./public

USER nextjs
EXPOSE 3000
CMD ["node", "server.js"]


