FROM node:20-alpine AS build
WORKDIR /app
# corepack 默认从 registry.npmjs.org 下载 pnpm 自身，阿里云服务器访问不通；
# 指定国内镜像源，否则 RUN pnpm install 会 ETIMEDOUT 失败
ENV COREPACK_NPM_REGISTRY=https://registry.npmmirror.com
RUN corepack enable
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml .npmrc ./
RUN pnpm install --frozen-lockfile
COPY . .
RUN pnpm build

FROM nginx:1.27-alpine
COPY --from=build /app/dist /usr/share/nginx/html
