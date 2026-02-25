package org.nowstart.evergreen;

import static org.mockito.Mockito.mockStatic;

import org.junit.jupiter.api.Test;
import org.mockito.MockedStatic;
import org.springframework.boot.SpringApplication;

class ApplicationTest {

    @Test
    void constructor_canBeInstantiatedForCoverage() {
        new Application();
    }

    @Test
    void main_invokesSpringApplicationRun() {
        String[] args = new String[] {"--spring.main.banner-mode=off"};

        try (MockedStatic<SpringApplication> springApplication = mockStatic(SpringApplication.class)) {
            Application.main(args);

            springApplication.verify(() -> SpringApplication.run(Application.class, args));
        }
    }
}
