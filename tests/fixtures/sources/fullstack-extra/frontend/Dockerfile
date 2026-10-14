FROM node:22 AS build
LABEL org.opencontainers.image.authors="jane.smith@corp.acme.com"
WORKDIR /src
COPY . .
RUN npm ci && npx ng build
FROM nginx:1.27
COPY --from=build /src/dist/paygw-portal/browser /usr/share/nginx/html
