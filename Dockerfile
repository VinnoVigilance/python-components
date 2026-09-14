FROM python:3.11-slim

WORKDIR /app

# Set display environment variable for Xvfb
ENV DISPLAY=:99

# Install system dependencies, X11 helper tools, and Google Chrome
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    libxml2-dev \
    libxslt1-dev \
    poppler-utils \
    xvfb \
    x11-utils \
    dbus-x11 \
    python3-tk \
    python3-dev \
    xauth \
    wget \
    gnupg \
    libglib2.0-0 \
    libnss3 \
    libfontconfig1 \
    libsecret-1-0 \
    libasound2 \
    && wget -q -O - https://dl-ssl.google.com/linux/linux_signing_key.pub | gpg --dearmor -o /usr/share/keyrings/googlechrome-linux-keyring.gpg \
    && echo "deb [arch=amd64 signed-by=/usr/share/keyrings/googlechrome-linux-keyring.gpg] http://dl.google.com/linux/chrome/deb/ stable main" >> /etc/apt/sources.list.d/google.list \
    && apt-get update \
    && apt-get install -y google-chrome-stable \
    && rm -rf /var/lib/apt/lists/*

# Copy and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 3. Pre-download SeleniumBase uc_driver during build so no internet is needed at runtime
RUN sbase get uc_driver

# Copy application source code
COPY . .


# Keeps container running quietly without executing any code on startup
CMD ["tail", "-f", "/dev/null"]


# Passive status check entrypoint
#CMD ["python", "-c", "print('Container ready. Service is waiting for execution.')"]
