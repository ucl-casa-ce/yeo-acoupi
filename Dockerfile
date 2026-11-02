# Use an official lightweight Python runtime as a parent image
FROM python:3.11-slim

# Set system environment variables to optimize Python within containers
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    STREAMLIT_SERVER_PORT=8501 \
    STREAMLIT_SERVER_ADDRESS=0.0.0.0

# Set the working directory inside the container
WORKDIR /app

# Install system utilities and update CA certificates (mitigates eMudhra/emSign CA incident)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    build-essential \
    && update-ca-certificates \
    && rm -rf /var/lib/apt/lists/*

ENV UV_SYSTEM_CERTS=true

# Copy the requirements file first to leverage Docker's caching mechanism
COPY requirements.txt .

# Upgrade pip and certifi to patched versions before installing dependencies
RUN pip install --no-cache-dir --upgrade "pip>=26.2.2" "certifi>=2026.10.19" && \
    pip install --no-cache-dir -r requirements.txt

# Ensure .streamlit folder and secrets.toml exist so environment variables can load
RUN mkdir -p .streamlit && touch .streamlit/secrets.toml

# Copy the application source code and reference data
COPY *.py .
COPY data/ ./data/

# Copy the local configuration folder if it exists in the build context
COPY .streamli[t]/ .streamlit/

# Expose the default Streamlit port
EXPOSE 8501

# Add a healthcheck to ensure the container is running and serving correctly
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
  CMD curl --fail http://localhost:8501/_stcore/health || exit 1

# Launch the Streamlit application
ENTRYPOINT ["streamlit", "run", "yeo-vis.py", "--server.enableCORS=false", "--server.enableXsrfProtection=false"]
