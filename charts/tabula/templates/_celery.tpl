{{/*
A Celery process (worker, beat or browser worker) as a Deployment.
Call with (dict "root" $ "name" "worker" "cfg" .Values.worker "image" <image>
"replicas" <n> "args" <list>).
*/}}
{{- define "tabula.celeryDeployment" -}}
{{- $root := .root -}}
{{- $cfg := .cfg -}}
apiVersion: apps/v1
kind: Deployment
metadata:
  name: {{ include "tabula.fullname" $root }}-{{ .name }}
  labels:
    {{- include "tabula.labels" $root | nindent 4 }}
    app.kubernetes.io/component: {{ .name }}
spec:
  replicas: {{ .replicas }}
  {{- if eq .name "beat" }}
  strategy:
    type: Recreate
  {{- end }}
  selector:
    matchLabels:
      {{- include "tabula.selectorLabels" (list $root .name) | nindent 6 }}
  template:
    metadata:
      annotations:
        {{- include "tabula.configChecksum" $root | nindent 8 }}
        {{- with $cfg.podAnnotations }}
        {{- toYaml . | nindent 8 }}
        {{- end }}
      labels:
        {{- include "tabula.labels" $root | nindent 8 }}
        app.kubernetes.io/component: {{ .name }}
    spec:
      serviceAccountName: {{ include "tabula.fullname" $root }}
      {{- with $root.Values.imagePullSecrets }}
      imagePullSecrets:
        {{- toYaml . | nindent 8 }}
      {{- end }}
      securityContext:
        {{- toYaml $cfg.podSecurityContext | nindent 8 }}
      containers:
        - name: {{ .name }}
          image: {{ .image | quote }}
          imagePullPolicy: {{ $root.Values.image.pullPolicy }}
          command:
            {{- toYaml .args | nindent 12 }}
          envFrom:
            {{- include "tabula.envFrom" $root | nindent 12 }}
          env:
            # Writable home for Chromium/Celery on a read-only root filesystem.
            - { name: HOME, value: /tmp }
          securityContext:
            {{- toYaml $cfg.securityContext | nindent 12 }}
          {{- with $cfg.livenessProbe }}
          livenessProbe:
            {{- toYaml . | nindent 12 }}
          {{- end }}
          resources:
            {{- toYaml $cfg.resources | nindent 12 }}
          volumeMounts:
            - { name: tmp, mountPath: /tmp }
      volumes:
        - name: tmp
          emptyDir: {}
      {{- with $cfg.nodeSelector }}
      nodeSelector:
        {{- toYaml . | nindent 8 }}
      {{- end }}
      {{- with $cfg.tolerations }}
      tolerations:
        {{- toYaml . | nindent 8 }}
      {{- end }}
      {{- with $cfg.affinity }}
      affinity:
        {{- toYaml . | nindent 8 }}
      {{- end }}
{{- end }}
