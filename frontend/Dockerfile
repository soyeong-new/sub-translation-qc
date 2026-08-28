FROM node:22 AS builder

WORKDIR /app

COPY package*.json .
RUN npm install

COPY . .
RUN npm run build

FROM caddy:2-alpine

COPY --from=builder /app/dist /usr/share/nginx/html
COPY Caddyfile /etc/caddy/Caddyfile

EXPOSE 80 443
