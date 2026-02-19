package org.nowstart.evergreen.backtest.runner;

import org.springframework.boot.WebApplicationType;
import org.springframework.boot.builder.SpringApplicationBuilder;
import org.springframework.boot.context.properties.ConfigurationPropertiesScan;
import org.springframework.context.annotation.ComponentScan;
import org.springframework.context.annotation.Configuration;

@Configuration
@ComponentScan(basePackages = "org.nowstart.evergreen.backtest")
@ConfigurationPropertiesScan(basePackages = "org.nowstart.evergreen.backtest")
public class BacktestMain {

    public static void main(String[] args) {
        try (var ignored = new SpringApplicationBuilder(BacktestMain.class)
                .web(WebApplicationType.NONE)
                .logStartupInfo(false)
                .properties(
                        "spring.cloud.config.enabled=false",
                        "spring.cloud.config.import-check.enabled=false"
                )
                .run(args)) {
            // ApplicationRunner beans execute during startup.
        }
    }
}
