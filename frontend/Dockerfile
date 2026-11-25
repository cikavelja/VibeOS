FROM node:22-alpine
WORKDIR /app
RUN chown node:node /app
USER node
COPY --chown=node:node package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY --chown=node:node . .
EXPOSE 5173
HEALTHCHECK --interval=5s --timeout=5s --start-period=10s --retries=5 \
  CMD ["node", "-e", "Promise.all([fetch('http://127.0.0.1:5173/'), fetch('http://127.0.0.1:5173/api/v1/health')]).then(async ([ui, api]) => { if (!ui.ok || !api.ok) process.exit(1); const body = await api.json(); if (body.status !== 'ok') process.exit(1); }).catch(() => process.exit(1))"]
CMD ["npm", "run", "dev", "--", "--port", "5173", "--strictPort"]
