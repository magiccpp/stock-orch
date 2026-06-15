FROM node:20-alpine AS builder

WORKDIR /app

# Install dependencies first to maximize layer caching.
COPY package*.json ./
RUN npm ci

# Build the Vite app. Set VITE_API_BASE_URL at build time if needed.
COPY . .
ARG VITE_API_BASE_URL=
ENV VITE_API_BASE_URL=${VITE_API_BASE_URL}
RUN npm run build

FROM nginx:1.27-alpine AS runtime

# Copy compiled static assets to nginx web root.
COPY --from=builder /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]