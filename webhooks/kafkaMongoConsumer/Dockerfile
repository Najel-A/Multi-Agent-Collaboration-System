FROM node:20-alpine

WORKDIR /app

COPY package.json package-lock.json* ./

RUN npm install --omit=dev

COPY consumer.js .

CMD ["node", "consumer.js"]