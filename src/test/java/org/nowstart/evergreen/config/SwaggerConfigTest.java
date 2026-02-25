package org.nowstart.evergreen.config;

import static org.assertj.core.api.Assertions.assertThat;

import java.util.Properties;
import org.junit.jupiter.api.Test;
import org.springframework.boot.info.BuildProperties;

class SwaggerConfigTest {

    @Test
    void customOpenAPI_buildsExpectedMetadata() {
        Properties properties = new Properties();
        properties.setProperty("version", "1.2.3");
        SwaggerConfig config = new SwaggerConfig(new BuildProperties(properties));

        var openApi = config.customOpenAPI();

        assertThat(openApi.getInfo().getTitle()).isEqualTo("evergreen API");
        assertThat(openApi.getInfo().getDescription()).contains("API 문서");
        assertThat(openApi.getInfo().getVersion()).isEqualTo("1.2.3");
    }
}
