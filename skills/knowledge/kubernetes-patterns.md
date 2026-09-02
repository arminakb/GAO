---
description: Kubernetes workload patterns — production Deployment template, probes, resources, RBAC, HPA/PDB, Jobs, and kubectl debugging.
tags: kubernetes, deployment, ops, containers
origin: ECC
---

# Kubernetes Patterns

Production-grade Kubernetes workload configuration: complete Deployment
template, probe decision table, resource sizing, least-privilege RBAC, and a
debugging cheatsheet. Pairs with `container-ops` (Docker image layer) and
`security-review`.

## Production Deployment Template

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: my-app
  namespace: my-namespace
  labels:
    app: my-app
    version: "1.0.0"
spec:
  replicas: 3
  selector:
    matchLabels:
      app: my-app
  strategy:
    type: RollingUpdate
    rollingUpdate:
      maxSurge: 1          # Allow 1 extra pod during update
      maxUnavailable: 0    # Never reduce below desired count
  template:
    metadata:
      labels:
        app: my-app
        version: "1.0.0"
    spec:
      securityContext:
        runAsNonRoot: true
        runAsUser: 1001
        fsGroup: 1001
      terminationGracePeriodSeconds: 30
      containers:
        - name: my-app
          image: ghcr.io/org/my-app:1.0.0   # Never :latest
          ports:
            - containerPort: 8080
          resources:                          # Requests AND limits — both required
            requests: { cpu: "100m", memory: "128Mi" }
            limits: { cpu: "500m", memory: "256Mi" }
          securityContext:
            allowPrivilegeEscalation: false
            readOnlyRootFilesystem: true
            capabilities:
              drop: [ALL]
          startupProbe:
            httpGet: { path: /health, port: 8080 }
            failureThreshold: 30              # 30 * 5s = 150s max startup
            periodSeconds: 5
          livenessProbe:
            httpGet: { path: /health, port: 8080 }
            periodSeconds: 30
            failureThreshold: 3
          readinessProbe:
            httpGet: { path: /ready, port: 8080 }   # Separate endpoint: checks DB, cache
            periodSeconds: 10
            failureThreshold: 2
          envFrom:
            - configMapRef: { name: my-app-config }
          env:
            - name: DB_PASSWORD
              valueFrom:
                secretKeyRef: { name: my-app-secrets, key: db-password }
          volumeMounts:
            - { name: tmp, mountPath: /tmp }   # Writable tmp when root fs is read-only
      volumes:
        - name: tmp
          emptyDir: {}
```

## Probes — Decision Table

| Probe | Failure action | Use for |
|-------|---------------|---------|
| `startupProbe` | Kills container if slow to start | Slow-starting apps (JVM, Python) |
| `livenessProbe` | Restarts container | Deadlock / hung process detection |
| `readinessProbe` | Removes from Service endpoints | Temporary unavailability (DB reconnect) |

Anti-pattern: `initialDelaySeconds: 60` on a liveness probe as a substitute
for a startup probe — arbitrary waits race the real startup time. Cover slow
startup with `startupProbe`; keep liveness/readiness delays near zero.

## Resource Requests and Limits

- **Requests** schedule the pod; **limits** throttle/kill it. Both required —
  limits without requests default requests to limits and over-reserve capacity.
- Rules of thumb: web API 100–250m CPU / 128–256Mi (limits 2–4× requests);
  workers 250–500m / 256–512Mi (memory limit = request for predictability);
  JVM apps need headroom above `-Xmx`; sidecars 10–50m / 32–64Mi.
- HPA requires `resources.requests` on all containers (utilization is
  computed as current/request).

## RBAC — Least Privilege

Two patterns:

- **App does not call the K8s API** (most apps): dedicated ServiceAccount
  with `automountServiceAccountToken: false`. No Role needed.
- **App calls the K8s API** (operators, watchers): token enabled + namespace
  `Role` (never `ClusterRole` unless cluster-wide is truly required), verbs
  limited to what is used (`get, list, watch`), secrets restricted by
  `resourceNames`, bound via `RoleBinding`.

## ConfigMaps and Secrets

- Non-sensitive config → ConfigMap (`envFrom` or file mount, read-only).
- Sensitive values → Secret via `secretKeyRef`; never plaintext in ConfigMaps.
- Raw Secrets are base64-encoded, not encrypted at rest — use Sealed Secrets
  or External Secrets Operator for production.

## HPA and PodDisruptionBudget

```yaml
# HPA: minReplicas >= 2 for HA; targets on CPU (70%) and memory (80%)
# PDB: survive node drains without downtime
apiVersion: policy/v1
kind: PodDisruptionBudget
spec:
  minAvailable: 2        # Never 0 — that defeats the purpose
  selector:
    matchLabels: { app: my-app }
```

## Jobs and CronJobs

- `restartPolicy: OnFailure` (never `Always` — infinite restart loop).
- Job: `backoffLimit: 3`, `ttlSecondsAfterFinished` for cleanup.
- CronJob: `concurrencyPolicy: Forbid` to prevent overlapping runs.

## kubectl Debugging Cheatsheet

```bash
kubectl describe pod <pod> -n ns        # Events: state, exit codes, OOMKilled
kubectl logs <pod> -n ns --previous     # Logs from the crashed container
kubectl top pods -n ns                  # Live resource usage
kubectl rollout undo deployment/my-app -n ns          # Rollback
kubectl get events -n ns --sort-by='.lastTimestamp'   # Cluster events
kubectl apply -f dep.yaml --dry-run=server            # Validate against live cluster
```

Common failures: **CrashLoopBackOff** → `logs --previous` + describe for exit
code; **ImagePullBackOff** → wrong tag or missing `imagePullSecrets`;
**Pending** → insufficient resources or taint/toleration mismatch;
**OOMKilled** → raise memory limits, check for leaks.

## Anti-Patterns (NEVER)

- `image: myapp:latest` — pin immutable tags or digests
- Running as root / default `securityContext: {}`
- No resources block — one pod can starve the node
- Plaintext secrets in ConfigMaps
- `cluster-admin` bound to an application ServiceAccount
- `restartPolicy: Always` in a Job

## Checklist

Security: non-root, read-only root fs, all capabilities dropped, dedicated SA,
token automount off unless needed, Role not ClusterRole, Sealed Secrets.

Reliability: all 3 probes, requests+limits on every container, `minReplicas: 2+`,
PDB on critical services, `maxUnavailable: 0` rolling updates.

Observability: `/health` + `/ready` endpoints, structured JSON logging (no PII),
`app`/`version`/`environment` labels.
