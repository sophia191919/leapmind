
 package com.treepeople.leapmindtts.service.optimize;
 
 import com.treepeople.leapmindtts.config.ContextCompressProperties;
 import com.treepeople.leapmindtts.config.PythonApiProperties;
 import com.treepeople.leapmindtts.pojo.dto.optimize.CompressRequest;
 import com.treepeople.leapmindtts.pojo.dto.optimize.CompressResponse;
 import io.github.resilience4j.circuitbreaker.annotation.CircuitBreaker;
 import io.github.resilience4j.timelimiter.annotation.TimeLimiter;
 import lombok.extern.slf4j.Slf4j;
 import org.springframework.beans.factory.annotation.Qualifier;
 import org.springframework.stereotype.Service;
 import org.springframework.web.reactive.function.client.WebClient;
 import reactor.core.publisher.Mono;
 
 @Slf4j
 @Service
 public class ContextCompressService {
     private final WebClient webClient;
     private final PythonApiProperties properties;
     private final ContextCompressProperties compressProperties;
     private final MetricsService metricsService; // Refactored
 
     public ContextCompressService(@Qualifier("contextCompressWebClient") WebClient webClient,
                                 PythonApiProperties properties,
                                 ContextCompressProperties compressProperties,
                                 MetricsService metricsService) {
         this.webClient = webClient;
         this.properties = properties;
         this.compressProperties = compressProperties;
         this.metricsService = metricsService;
     }

    @TimeLimiter(name = "contextCompress")
    @CircuitBreaker(name = "contextCompress", fallbackMethod = "compressContextFallback")
    public Mono<String> compressContext(String contextText) {
        // A simple heuristic to estimate token count. A more accurate tokenizer would be better.
        int estimatedTokens = (int) (contextText.length() / 2.0);
        if (estimatedTokens < compressProperties.getTokenThreshold()) {
            log.debug("Token count {} is below threshold {}, skipping compression.", estimatedTokens, compressProperties.getTokenThreshold());
            return Mono.just(contextText);
        }

        log.info("Token count {} exceeds threshold {}, attempting to compress context.", estimatedTokens, compressProperties.getTokenThreshold());
        CompressRequest request = new CompressRequest(contextText, compressProperties.getMaxCompressedTokens());

        return webClient.post()
            .uri(properties.getCompressContextUri())
            .bodyValue(request)
            .retrieve()
            .bodyToMono(CompressResponse.class)
            .map(CompressResponse::getCompressedText)
            .transform(metricsService.recordMonoDuration("external.api.duration", "api", "compress"))
            .doOnSuccess(r -> log.info("Context compression successful."))
            .doOnError(e -> log.error("Context compression failed.", e));
    }

    /**
     * Fallback for context compression. If the service fails, it logs the error
     * and returns the original, uncompressed text.
     */
    public Mono<String> compressContextFallback(String contextText, Throwable t) {
        log.warn("Context compression fallback activated. Returning original context. Reason: {}", t.getMessage());
        metricsService.incrementFallback("contextCompress");
        return Mono.just(contextText);
    }
}
