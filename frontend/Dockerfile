# --- 构建阶段 ---
FROM node:22-alpine AS builder
WORKDIR /app
RUN corepack enable
COPY package.json pnpm-lock.yaml ./
RUN pnpm config set registry https://registry.npmmirror.com && \
    pnpm install --frozen-lockfile --ignore-scripts
COPY . .
ARG NEXT_PUBLIC_API_BASE=""
ENV NEXT_PUBLIC_API_BASE=$NEXT_PUBLIC_API_BASE
RUN pnpm build

# --- 运行阶段（直接 copy .next，不重新 pnpm install） ---
FROM node:22-alpine AS runner
WORKDIR /app
RUN corepack enable
COPY --from=builder /app/package.json /app/pnpm-lock.yaml ./
COPY --from=builder /app/.next ./.next
COPY --from=builder /app/node_modules ./node_modules
COPY --from=builder /app/public ./public

EXPOSE 3000
CMD ["pnpm", "start"]
