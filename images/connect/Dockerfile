ARG CURL_IMAGE=curlimages/curl:8.22.0
ARG DEBEZIUM_CONNECT_IMAGE=quay.io/debezium/connect:3.6.2.Final
ARG DBZ_JDBC_PLUGIN_VERSION=3.6.2.Final
ARG JMX_EXPORTER_VERSION=1.0.1

FROM ${CURL_IMAGE} AS dl
ARG DBZ_JDBC_PLUGIN_VERSION
ARG JMX_EXPORTER_VERSION
RUN curl -fsSL -o /tmp/jdbc-plugin.tar.gz \
      "https://repo1.maven.org/maven2/io/debezium/debezium-connector-jdbc/${DBZ_JDBC_PLUGIN_VERSION}/debezium-connector-jdbc-${DBZ_JDBC_PLUGIN_VERSION}-plugin.tar.gz" \
 && mkdir -p /tmp/plugins \
 && tar -xzf /tmp/jdbc-plugin.tar.gz -C /tmp/plugins \
 && curl -fsSL -o /tmp/jmx_prometheus_javaagent.jar \
      "https://repo1.maven.org/maven2/io/prometheus/jmx/jmx_prometheus_javaagent/${JMX_EXPORTER_VERSION}/jmx_prometheus_javaagent-${JMX_EXPORTER_VERSION}.jar"

FROM ${DEBEZIUM_CONNECT_IMAGE}
USER root
# El tar del plugin extrae a una carpeta debezium-connector-jdbc/
COPY --from=dl /tmp/plugins/debezium-connector-jdbc/ /kafka/connect/debezium-connector-jdbc/
COPY --from=dl /tmp/jmx_prometheus_javaagent.jar /opt/jmx/jmx_prometheus_javaagent.jar
COPY config/jmx/connect-jmx.yaml /opt/jmx/connect-jmx.yaml
COPY images/connect/entrypoint.sh /kafka/bin/cdc-entrypoint.sh
RUN chmod +x /kafka/bin/cdc-entrypoint.sh \
 && echo "--- plugins en /kafka/connect ---" && ls /kafka/connect/ \
 && echo "--- jdbc sink ---" && ls /kafka/connect/debezium-connector-jdbc/ | head -5
EXPOSE 8083 7072
ENTRYPOINT ["/kafka/bin/cdc-entrypoint.sh"]
