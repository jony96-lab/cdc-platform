ARG CURL_IMAGE=curlimages/curl:8.22.0
ARG KAFKA_BASE_IMAGE=apache/kafka:4.3.1
ARG JMX_EXPORTER_VERSION=1.0.1

FROM ${CURL_IMAGE} AS dl
ARG JMX_EXPORTER_VERSION
RUN curl -fsSL -o /tmp/jmx_prometheus_javaagent.jar \
      "https://repo1.maven.org/maven2/io/prometheus/jmx/jmx_prometheus_javaagent/${JMX_EXPORTER_VERSION}/jmx_prometheus_javaagent-${JMX_EXPORTER_VERSION}.jar"

FROM ${KAFKA_BASE_IMAGE}
COPY --from=dl /tmp/jmx_prometheus_javaagent.jar /opt/jmx/jmx_prometheus_javaagent.jar
COPY config/jmx/kafka-jmx.yaml /opt/jmx/kafka-jmx.yaml
EXPOSE 9092 7071
