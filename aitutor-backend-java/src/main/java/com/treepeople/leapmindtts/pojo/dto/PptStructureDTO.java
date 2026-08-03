package com.treepeople.leapmindtts.pojo.dto;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.databind.PropertyNamingStrategies.SnakeCaseStrategy;
import com.fasterxml.jackson.databind.annotation.JsonNaming;
import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.util.List;

@Data
@Builder
@NoArgsConstructor
@AllArgsConstructor
@JsonNaming(SnakeCaseStrategy.class)
public class PptStructureDTO {

    /** 备课ID（Python的generate-ppt返回的pptId，值等于prepId） */
    private Integer pptId;

    /** 演示文稿总标题（如Python只传slides而没传title，则会从type=cover的slide提取） */
    private String title;

    /** 副标题（封面页下方） */
    private String description;

    /** 幻灯片列表（可能包含type=cover的封面页，导出时会自动跳过封面页，避免和buildTitleSlide重复） */
    private List<SlideDTO> slides;

    /** 模板样式配置（可选，null时使用默认蓝色主题） */
    private TemplateConfig templateConfig;

    @Data
    @Builder
    @NoArgsConstructor
    @AllArgsConstructor
    @JsonIgnoreProperties(ignoreUnknown = true)
    @JsonNaming(SnakeCaseStrategy.class)
    public static class SlideDTO {

        /** 页码，从1开始 */
        private Integer pageNum;

        /**
         * 幻灯片类型（枚举）：
         * cover      封面页 → 导出时作为整个PPT的title/description来源，不单独画成内容页
         * content    内容页 → 正常要点讲解
         * interactive互动页 → 附加展示question/options/answer
         * summary    总结页 → 标题前加"📌"标记
         * homework   课后作业页 → 标题前加"📝"标记
         */
        private String type;

        /** 单页标题 */
        private String title;

        /** 纯正文（bulletPoints为空时使用，支持\n换行） */
        private String content;

        /** 要点列表，1~7条，每行前自动加项目符号 */
        private List<String> bulletPoints;

        /** 教师讲解词（Stage3输出，用于TTS） */
        private String notes;

        /** 配图URL（保留） */
        private String imageUrl;

        // ---------- 以下是Python generate-ppt 新增返回字段 ----------

        /** 配图提示文案（可供AI生图或前端占位，POI渲染时暂不画图片） */
        private String imageSuggestion;

        /** LaTeX公式（POI渲染时在正文下方单独显示一行加粗） */
        private String formula;

        /** 需要高亮的关键词列表（在正文底部追加"关键词：xxx、yyy"形式） */
        private List<String> highlightPoints;

        /** 互动题（type=interactive时不为null） */
        private InteractionDTO interaction;

        /** AI生成失败回退标记（true时前端显示重试提示，POI导出照常） */
        private Boolean isFallback;
    }

    /**
     * 互动题结构（type=interactive时使用）
     */
    @Data
    @Builder
    @NoArgsConstructor
    @AllArgsConstructor
    @JsonIgnoreProperties(ignoreUnknown = true)
    @JsonNaming(SnakeCaseStrategy.class)
    public static class InteractionDTO {
        /** 互动类型：choice_question / think_question / practice */
        private String type;
        /** 问题文本 */
        private String question;
        /** 选项列表（选择题必填） */
        private List<String> options;
        /** 正确答案或参考答案 */
        private String answer;
    }

    @Data
    @Builder
    @NoArgsConstructor
    @AllArgsConstructor
    public static class TemplateConfig {

        private String primaryColor;

        private String secondaryColor;

        private String backgroundColor;

        private String titleFont;

        private String contentFont;

        private Integer titleFontSize;

        private Integer contentFontSize;
    }
}
