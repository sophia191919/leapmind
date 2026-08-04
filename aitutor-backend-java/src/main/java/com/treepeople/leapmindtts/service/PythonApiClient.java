package com.treepeople.leapmindtts.service;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.annotation.JsonProperty;
import com.fasterxml.jackson.databind.ObjectMapper;
import lombok.Data;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.MediaType;
import org.springframework.stereotype.Service;
import org.springframework.web.reactive.function.client.WebClient;
import reactor.core.publisher.Mono;

import java.time.Duration;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * [跨端联桥] Java → Python HTTP 调用客户端。
 * 对应 Python 端 internal_ai.py: POST /api/internal/ai/generate-lesson-prep
 *
 * <h3>数据格式说明</h3>
 * <ul>
 *   <li>请求：Jackson 序列化时 @JsonProperty 将 camelCase 字段转为 snake_case 发送给 Python</li>
 *   <li>响应：Python 返回 snake_case（如 prep_id, total_pages, page_num），
 *            本类在 parse 后不走 Jackson 绑定，手动提取并统一转为 camelCase，
 *            保证存入 DB 的 slides JSON 与 PptStructureDTO.parse() 兼容</li>
 * </ul>
 */
@Slf4j
@Service
@RequiredArgsConstructor
public class PythonApiClient {

    private final ObjectMapper om;
    private final WebClient.Builder webClientBuilder;

    @Value("${python.service.base-url:http://localhost:8001}")
    private String pythonBaseUrl;

    /**
     * [跨端联桥] 调用 Python 非流式备课生成。
     *
     * @param request 备课参数（title/subject/grade/knowledgePointIds 等）
     * @return LessonPrepResponse，其中 slides 已转为 camelCase
     */
    public LessonPrepResponse generateLessonPrep(LessonPrepRequest request) {
        log.info("[联桥] 调用 Python 备课生成, title={}, subject={}, grade={}",
                request.getTitle(), request.getSubject(), request.getGrade());

        WebClient client = webClientBuilder
                .baseUrl(pythonBaseUrl)
                .build();

        try {
            @SuppressWarnings("unchecked")
            Map<String, Object> raw = client.post()
                    .uri("/api/internal/ai/generate-lesson-prep")
                    .contentType(MediaType.APPLICATION_JSON)
                    .bodyValue(request)
                    .retrieve()
                    .onStatus(status -> status.isError(), resp ->
                            resp.bodyToMono(String.class)
                                    .flatMap(body -> Mono.error(
                                            new RuntimeException("Python API 返回错误: " + body))))
                    .bodyToMono(Map.class)
                    .timeout(Duration.ofSeconds(180))
                    .block();

            if (raw == null) {
                throw new RuntimeException("Python 返回为空");
            }
            if (raw.containsKey("error")) {
                throw new RuntimeException("Python 备课生成失败: " + raw.get("error"));
            }

            LessonPrepResponse response = new LessonPrepResponse();
            response.setPrepId(toInt(raw.get("prep_id")));
            response.setTotalPages(toInt(raw.get("total_pages")));

            // syllabus 原样保留（Java 不解析其内部结构，只透传存入 generated_content_json）
            @SuppressWarnings("unchecked")
            Map<String, Object> syllabus = (Map<String, Object>) raw.get("syllabus");
            response.setSyllabus(syllabus);

            // slides 需要把 key 从 snake_case → camelCase，否则 PptStructureDTO.parse() 不认
            @SuppressWarnings("unchecked")
            List<Map<String, Object>> rawSlides = (List<Map<String, Object>>) raw.get("slides");
            if (rawSlides != null) {
                List<Map<String, Object>> camelSlides = new ArrayList<>();
                for (Map<String, Object> s : rawSlides) {
                    camelSlides.add(convertKeysToCamel(s));
                }
                response.setSlides(camelSlides);
            } else {
                response.setSlides(List.of());
            }

            log.info("[联桥] Python 备课生成成功, prepId={}, totalPages={}",
                    response.getPrepId(), response.getTotalPages());
            return response;

        } catch (Exception e) {
            log.error("[联桥] 调用 Python 备课生成失败", e);
            throw new RuntimeException("Python AI 备课生成失败: " + e.getMessage(), e);
        }
    }

    /**
     * 将 map 的所有 key 从 snake_case 转为 camelCase（递归处理嵌套 map/list）。
     * 例如 page_num→pageNum, bullet_points→bulletPoints。
     */
    @SuppressWarnings("unchecked")
    private Map<String, Object> convertKeysToCamel(Map<String, Object> snakeMap) {
        Map<String, Object> result = new LinkedHashMap<>();
        for (Map.Entry<String, Object> entry : snakeMap.entrySet()) {
            String camelKey = snakeToCamel(entry.getKey());
            Object value = entry.getValue();
            if (value instanceof Map) {
                value = convertKeysToCamel((Map<String, Object>) value);
            } else if (value instanceof List) {
                List<Object> list = (List<Object>) value;
                List<Object> converted = new ArrayList<>();
                for (Object item : list) {
                    if (item instanceof Map) {
                        converted.add(convertKeysToCamel((Map<String, Object>) item));
                    } else {
                        converted.add(item);
                    }
                }
                value = converted;
            }
            result.put(camelKey, value);
        }
        return result;
    }

    private String snakeToCamel(String snake) {
        StringBuilder sb = new StringBuilder();
        boolean upper = false;
        for (int i = 0; i < snake.length(); i++) {
            char c = snake.charAt(i);
            if (c == '_') {
                upper = true;
            } else if (upper) {
                sb.append(Character.toUpperCase(c));
                upper = false;
            } else {
                sb.append(c);
            }
        }
        return sb.toString();
    }

    private int toInt(Object val) {
        if (val instanceof Number) return ((Number) val).intValue();
        if (val instanceof String) return Integer.parseInt((String) val);
        return 0;
    }

    // ========== 请求/响应 DTO ==========

    /**
     * 发给 Python POST /api/internal/ai/generate-lesson-prep 的请求体。
     * Python LessonPrepInternalRequest 用 snake_case，所以每个字段加 @JsonProperty。
     */
    @Data
    @JsonIgnoreProperties(ignoreUnknown = true)
    public static class LessonPrepRequest {
        @JsonProperty("user_id")
        private int userId;

        @JsonProperty("title")
        private String title;

        @JsonProperty("subject")
        private String subject;

        @JsonProperty("grade")
        private String grade;

        @JsonProperty("knowledge_point_ids")
        private List<Integer> knowledgePointIds;

        @JsonProperty("teaching_goals")
        private List<String> teachingGoals;

        @JsonProperty("total_hours")
        private int totalHours = 1;

        @JsonProperty("style")
        private String style = "standard";

        @JsonProperty("weak_point_ids")
        private List<Integer> weakPointIds;

        @JsonProperty("user_profile_summary")
        private String userProfileSummary;
    }

    /**
     * Python generate_and_return() 返回的数据（经过本 client 已统一转为 camelCase）。
     */
    @Data
    public static class LessonPrepResponse {
        private int prepId;
        private int totalPages;
        private Map<String, Object> syllabus;
        private List<Map<String, Object>> slides;
    }
}
