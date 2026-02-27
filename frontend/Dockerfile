FROM node:20-alpine

WORKDIR /app

# Copy package files
COPY package*.json ./

# Install dependencies (npm ci for reproducible builds, RT-18)
RUN npm ci

# Copy application code
COPY . .

# SA2-16: Run as non-root user (node user is built into node:20-alpine)
# Ensure node user owns /app so npm ci works at runtime with Docker volume mounts
RUN chown -R node:node /app
USER node

# Expose port
EXPOSE 5173

# SA3-M8: Dependencies are installed at build time (RUN npm ci above).
# Do NOT run npm ci at startup — it re-downloads packages on every restart,
# creating a supply-chain risk window.  The Docker volume for node_modules
# persists the build-time install.
CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0"]
