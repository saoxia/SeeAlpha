# Next.js standalone bundle, prebuilt with Docker-internal API routing.
FROM node:22-bookworm-slim AS builder
WORKDIR /app/web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
ENV SEEALPHA_API_URL=http://platform:8000
RUN npm run build

FROM node:22-bookworm-slim
ENV NODE_ENV=production HOSTNAME=0.0.0.0 PORT=3000
WORKDIR /app
COPY --from=builder /app/web/.next/standalone ./
COPY --from=builder /app/web/.next/static ./.next/static
COPY --from=builder /app/web/public ./public
USER node
EXPOSE 3000
CMD ["node", "server.js"]
