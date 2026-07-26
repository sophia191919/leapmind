
 package com.treepeople.leapmindtts.service.optimize;
 
 import com.github.benmanes.caffeine.cache.Cache;
 import com.github.benmanes.caffeine.cache.Caffeine;
 import com.github.benmanes.caffeine.cache.RemovalCause;
 import io.micrometer.core.instrument.MeterRegistry;
 import io.micrometer.core.instrument.Tags;
 import lombok.AllArgsConstructor;
 import lombok.Data;
 import lombok.extern.slf4j.Slf4j;
 import org.springframework.stereotype.Service;
 
 import java.util.concurrent.CompletableFuture;
 import java.util.concurrent.TimeUnit;
 
 @Slf4j
 @Service
 public class RequestMergeService {
 
     // 存储处理中的请求：dedupKey -> CompletableFuture<处理结果>
     private final Cache<String, CompletableFuture<String>> pendingRequests;
 
     public RequestMergeService(MeterRegistry meterRegistry) {
         this.pendingRequests = Caffeine.newBuilder()
             .expireAfterWrite(300, TimeUnit.MILLISECONDS) // 300ms 后自动过期清除
             .maximumSize(10000)
             .removalListener((key, value, cause) -> {
                 if (cause == RemovalCause.EXPIRED && value != null) {
                     @SuppressWarnings("unchecked")
                     CompletableFuture<String> future = (CompletableFuture<String>) value;
                     if (!future.isDone()) {
                         future.cancel(true); // 过期未完成自动中断
                     }
                 }
             })
             .build();
         
         meterRegistry.gauge("request.merge.pool.size", 
             Tags.of("name", "pendingRequests"), 
             this.pendingRequests, 
             cache -> cache.estimatedSize());
     }
 
     public RequestMergeResult tryMerge(String dedupKey) {
         CompletableFuture<String> existingFuture = pendingRequests.getIfPresent(dedupKey);
         if (existingFuture != null && !existingFuture.isDone()) {
             log.debug("请求合并：key={} 等待已有请求结果", dedupKey);
             return RequestMergeResult.merged(existingFuture);
         }
 
         CompletableFuture<String> newFuture = new CompletableFuture<>();
         pendingRequests.put(dedupKey, newFuture);
         log.debug("请求合并：key={} 作为首个请求", dedupKey);
         return RequestMergeResult.first(newFuture);
     }
 
     public void completeRequest(String dedupKey, String result) {
         CompletableFuture<String> future = pendingRequests.getIfPresent(dedupKey);
         if (future != null && !future.isDone()) {
             future.complete(result);
         }
         pendingRequests.invalidate(dedupKey);
     }
 
     public void failRequest(String dedupKey, Throwable ex) {
         CompletableFuture<String> future = pendingRequests.getIfPresent(dedupKey);
         if (future != null && !future.isDone()) {
             future.completeExceptionally(ex);
         }
         pendingRequests.invalidate(dedupKey);
     }
 
     @Data
     @AllArgsConstructor
     public static class RequestMergeResult {
         private boolean isFirst;
         private CompletableFuture<String> future;
         public static RequestMergeResult first(CompletableFuture<String> f) { return new RequestMergeResult(true, f); }
         public static RequestMergeResult merged(CompletableFuture<String> f) { return new RequestMergeResult(false, f); }
     }
 }
 