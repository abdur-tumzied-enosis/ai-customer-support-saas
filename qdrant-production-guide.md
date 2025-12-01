# Qdrant Production Hosting Guide

This guide covers different options for hosting Qdrant in production.

## Option 1: Qdrant Cloud (Recommended for Most Cases)

**Pros:**
- Fully managed service
- Automatic backups and updates
- Built-in monitoring and scaling
- High availability options
- No infrastructure management

**Steps:**
1. Sign up at https://cloud.qdrant.io
2. Create a cluster (choose region closest to your app)
3. Get your cluster URL and API key
4. Update your `.env` file:
   ```bash
   QDRANT_URL=https://your-cluster-id.qdrant.io
   QDRANT_API_KEY=your-api-key-here
   ```

**Cost:** Pay-as-you-go, starts around $25/month for small clusters

---

## Option 2: Self-Hosted with Docker Compose

### Basic Production Setup

1. **Create a production docker-compose file** (see `docker-compose.prod.yml`)

2. **Set up persistent storage:**
   ```bash
   sudo mkdir -p /var/lib/qdrant
   sudo chown -R 1000:1000 /var/lib/qdrant  # Qdrant runs as UID 1000
   ```

3. **Generate API key:**
   ```bash
   # Generate a secure API key
   openssl rand -hex 32
   ```

4. **Update your `.env` file:**
   ```bash
   QDRANT_URL=http://your-server-ip:6333
   QDRANT_API_KEY=your-generated-api-key
   ```

5. **Deploy:**
   ```bash
   docker-compose -f docker-compose.prod.yml up -d qdrant
   ```

### Security Best Practices

- **Enable API key authentication** (already in docker-compose.prod.yml)
- **Use a reverse proxy** (Nginx/Traefik) with SSL/TLS
- **Restrict network access** - only allow connections from your Django app
- **Regular backups** - backup the `/var/lib/qdrant` directory
- **Monitor disk space** - Qdrant can grow large with many vectors

### Nginx Reverse Proxy Example

```nginx
# /etc/nginx/sites-available/qdrant
server {
    listen 443 ssl http2;
    server_name qdrant.yourdomain.com;

    ssl_certificate /path/to/cert.pem;
    ssl_certificate_key /path/to/key.pem;

    location / {
        proxy_pass http://localhost:6333;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        
        # Increase timeouts for large requests
        proxy_connect_timeout 300s;
        proxy_send_timeout 300s;
        proxy_read_timeout 300s;
    }
}
```

Then update `QDRANT_URL` to `https://qdrant.yourdomain.com`

---

## Option 3: Kubernetes Deployment

For larger scale deployments, use Kubernetes:

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: qdrant
spec:
  replicas: 1  # Use 3+ for high availability
  selector:
    matchLabels:
      app: qdrant
  template:
    metadata:
      labels:
        app: qdrant
    spec:
      containers:
      - name: qdrant
        image: qdrant/qdrant:v1.9.1
        ports:
        - containerPort: 6333
        - containerPort: 6334
        env:
        - name: QDRANT__SERVICE__API_KEY
          valueFrom:
            secretKeyRef:
              name: qdrant-secrets
              key: api-key
        volumeMounts:
        - name: qdrant-storage
          mountPath: /qdrant/storage
      volumes:
      - name: qdrant-storage
        persistentVolumeClaim:
          claimName: qdrant-pvc
---
apiVersion: v1
kind: Service
metadata:
  name: qdrant-service
spec:
  selector:
    app: qdrant
  ports:
  - port: 6333
    targetPort: 6333
  type: ClusterIP
```

---

## Option 4: Standalone Server Installation

For dedicated servers without Docker:

1. **Download Qdrant binary:**
   ```bash
   wget https://github.com/qdrant/qdrant/releases/download/v1.9.1/qdrant-x86_64-unknown-linux-gnu
   chmod +x qdrant-x86_64-unknown-linux-gnu
   sudo mv qdrant-x86_64-unknown-linux-gnu /usr/local/bin/qdrant
   ```

2. **Create systemd service** (`/etc/systemd/system/qdrant.service`):
   ```ini
   [Unit]
   Description=Qdrant Vector Database
   After=network.target

   [Service]
   Type=simple
   User=qdrant
   ExecStart=/usr/local/bin/qdrant
   Restart=always
   RestartSec=5
   Environment="QDRANT__SERVICE__API_KEY=your-api-key"
   Environment="QDRANT__STORAGE__STORAGE_PATH=/var/lib/qdrant"

   [Install]
   WantedBy=multi-user.target
   ```

3. **Start service:**
   ```bash
   sudo systemctl enable qdrant
   sudo systemctl start qdrant
   ```

---

## Updating Your Django Application

### 1. Update Settings for API Key Support

Your current `vector_store.py` uses `QdrantClient(url=...)`. Update it to support API keys:

```python
def get_qdrant_client() -> QdrantClient:
    api_key = getattr(settings, 'QDRANT_API_KEY', None)
    if api_key:
        return QdrantClient(url=settings.QDRANT_URL, api_key=api_key)
    return QdrantClient(url=settings.QDRANT_URL)
```

### 2. Environment Variables

Add to your `.env` file:
```bash
# For Qdrant Cloud or self-hosted with auth
QDRANT_URL=https://your-cluster.qdrant.io  # or http://your-server:6333
QDRANT_API_KEY=your-api-key-here  # Optional but recommended
```

---

## Backup Strategy

### Manual Backup
```bash
# Stop Qdrant
docker-compose stop qdrant

# Backup storage directory
tar -czf qdrant-backup-$(date +%Y%m%d).tar.gz /var/lib/qdrant

# Start Qdrant
docker-compose start qdrant
```

### Automated Backup Script
```bash
#!/bin/bash
# backup-qdrant.sh
BACKUP_DIR="/backups/qdrant"
DATE=$(date +%Y%m%d_%H%M%S)
mkdir -p $BACKUP_DIR

# Create snapshot (Qdrant supports snapshots)
docker exec qdrant-prod curl -X POST "http://localhost:6333/collections/{collection_name}/snapshots"

# Or backup entire storage
tar -czf $BACKUP_DIR/qdrant-$DATE.tar.gz /var/lib/qdrant

# Keep only last 7 days
find $BACKUP_DIR -name "qdrant-*.tar.gz" -mtime +7 -delete
```

Add to crontab:
```bash
0 2 * * * /path/to/backup-qdrant.sh
```

---

## Monitoring

### Health Check Endpoint
```bash
curl http://localhost:6333/health
```

### Metrics Endpoint
```bash
curl http://localhost:6333/metrics
```

### Recommended Monitoring Tools
- **Prometheus** - for metrics collection
- **Grafana** - for visualization
- **Qdrant Cloud** - built-in monitoring if using cloud

---

## Performance Tuning

### Resource Requirements
- **Small deployments** (< 1M vectors): 2 CPU, 4GB RAM
- **Medium deployments** (1M-10M vectors): 4 CPU, 8GB RAM
- **Large deployments** (> 10M vectors): 8+ CPU, 16GB+ RAM

### Configuration Tuning
Edit `qdrant/config/production.yaml`:
```yaml
storage:
  # Use memory-mapped files for better performance
  storage_path: /qdrant/storage
  
  # Optimize for your workload
  optimizers:
    indexing_threshold: 20000
    flush_interval_sec: 5

service:
  max_request_size_mb: 64  # Increase for large batch inserts
  max_workers: 4  # Adjust based on CPU cores
```

---

## Migration from Development to Production

1. **Export collections** (if needed):
   ```bash
   # Use Qdrant's snapshot feature
   curl -X POST "http://localhost:6333/collections/{collection_name}/snapshots"
   ```

2. **Update Django settings** with production QDRANT_URL

3. **Test connection**:
   ```python
   python manage.py shell
   >>> from agents.vector_store import get_qdrant_client
   >>> client = get_qdrant_client()
   >>> client.get_collections()
   ```

---

## Troubleshooting

### Common Issues

1. **Connection refused**
   - Check firewall rules
   - Verify Qdrant is running: `docker ps | grep qdrant`
   - Check logs: `docker logs qdrant-prod`

2. **Authentication errors**
   - Verify API key matches in both Qdrant config and Django settings
   - Check API key format (no extra spaces)

3. **Out of memory**
   - Increase container memory limits
   - Reduce `indexing_threshold` in config
   - Consider using Qdrant Cloud for managed scaling

4. **Slow queries**
   - Check disk I/O: `iostat -x 1`
   - Consider SSD storage
   - Optimize collection parameters (HNSW m, ef_construct)

---

## Recommended Approach

For most production scenarios, **Qdrant Cloud** is the best choice because:
- No infrastructure management overhead
- Built-in backups and monitoring
- Easy scaling
- Professional support available

Use self-hosted only if:
- You have specific compliance requirements
- You need full control over data location
- You have existing infrastructure and DevOps expertise
- Cost is a primary concern (though cloud pricing is competitive)


