{{- define "tabula.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "tabula.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{- define "tabula.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "tabula.labels" -}}
helm.sh/chart: {{ include "tabula.chart" . }}
app.kubernetes.io/name: {{ include "tabula.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/* Selector labels for one component: include "tabula.selectorLabels" (list . "backend") */}}
{{- define "tabula.selectorLabels" -}}
{{- $root := index . 0 -}}
app.kubernetes.io/name: {{ include "tabula.name" $root }}
app.kubernetes.io/instance: {{ $root.Release.Name }}
app.kubernetes.io/component: {{ index . 1 }}
{{- end }}

{{- define "tabula.tag" -}}
{{- .Values.image.tag | default .Chart.AppVersion | default "latest" }}
{{- end }}

{{- define "tabula.backendImage" -}}
{{- printf "%s:%s" .Values.image.backend (include "tabula.tag" .) }}
{{- end }}

{{- define "tabula.frontendImage" -}}
{{- printf "%s:%s" .Values.image.frontend (include "tabula.tag" .) }}
{{- end }}

{{- define "tabula.secretName" -}}
{{- .Values.existingSecret | default (printf "%s-secrets" (include "tabula.fullname" .)) }}
{{- end }}

{{- define "tabula.postgresHost" -}}
{{- if .Values.postgresql.enabled }}
{{- printf "%s-postgres" (include "tabula.fullname" .) }}
{{- else }}
{{- required "externalDatabase.host is required when postgresql.enabled is false" .Values.externalDatabase.host }}
{{- end }}
{{- end }}

{{- define "tabula.postgresPort" -}}
{{- if .Values.postgresql.enabled }}5432{{ else }}{{ .Values.externalDatabase.port }}{{ end }}
{{- end }}

{{- define "tabula.postgresUser" -}}
{{- if .Values.postgresql.enabled }}{{ .Values.postgresql.user }}{{ else }}{{ .Values.externalDatabase.user }}{{ end }}
{{- end }}

{{- define "tabula.postgresDatabase" -}}
{{- if .Values.postgresql.enabled }}{{ .Values.postgresql.database }}{{ else }}{{ .Values.externalDatabase.database }}{{ end }}
{{- end }}

{{- define "tabula.redisUrl" -}}
{{- if .Values.redis.enabled }}
{{- printf "redis://%s-redis:6379/0" (include "tabula.fullname" .) }}
{{- else }}
{{- required "externalRedis.url is required when redis.enabled is false" .Values.externalRedis.url }}
{{- end }}
{{- end }}

{{/* envFrom shared by the backend, worker and migration init container */}}
{{- define "tabula.envFrom" -}}
- configMapRef:
    name: {{ include "tabula.fullname" . }}-config
- secretRef:
    name: {{ include "tabula.secretName" . }}
{{- end }}

{{/* Restart pods when the chart-managed config or secret changes */}}
{{- define "tabula.configChecksum" -}}
checksum/config: {{ include (print $.Template.BasePath "/configmap.yaml") . | sha256sum }}
{{- if not .Values.existingSecret }}
checksum/secret: {{ include (print $.Template.BasePath "/secret.yaml") . | sha256sum }}
{{- end }}
{{- end }}
