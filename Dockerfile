# ============================================
# SolArb — Backend (Docker)
# ============================================
FROM node:20-alpine AS builder
WORKDIR /app
COPY backend/package*.json ./
RUN npm ci --prod
COPY backend/src ./src

FROM node:20-alpine
WORKDIR /app
COPY --from=builder /app/node_modules ./node_modules
COPY --from=builder /app/src ./src
EXPOSE 3001
CMD ["node", "src/server.js"]
