package com.treepeople.leapmindtts.pojo.dto;

import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.util.List;

@Data
@Builder
@NoArgsConstructor
@AllArgsConstructor
public class PptStructureDTO {

    private String title;

    private String description;

    private List<SlideDTO> slides;

    private TemplateConfig templateConfig;

    @Data
    @Builder
    @NoArgsConstructor
    @AllArgsConstructor
    public static class SlideDTO {

        private String title;

        private String content;

        private List<String> bulletPoints;

        private String notes;

        private String imageUrl;
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
