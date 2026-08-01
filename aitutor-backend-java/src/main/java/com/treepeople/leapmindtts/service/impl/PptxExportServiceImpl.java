package com.treepeople.leapmindtts.service.impl;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.treepeople.leapmindtts.mapper.TeachingContentMapper;
import com.treepeople.leapmindtts.pojo.dto.PptStructureDTO;
import com.treepeople.leapmindtts.pojo.entity.TeachingContent;
import com.treepeople.leapmindtts.service.PptxExportService;
import io.minio.MinioClient;
import io.minio.PutObjectArgs;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.apache.poi.xslf.usermodel.*;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;

import java.awt.*;
import java.io.*;
import java.time.LocalDateTime;
import java.util.List;
import java.util.UUID;
import java.time.format.DateTimeFormatter;

@Slf4j
@Service
@RequiredArgsConstructor
public class PptxExportServiceImpl implements PptxExportService {

    private final TeachingContentMapper teachingContentMapper;
    private final MinioClient minioClient;
    private final ObjectMapper objectMapper;

    @Value("${minio.bucket-name:leapmind}")
    private String bucketName;

    @Value("${minio.endpoint:http://127.0.0.1:9000}")
    private String endpoint;

    private static final String PPT_OBJECT_PREFIX = "ppt-exports/";
    private static final DateTimeFormatter DATE_FMT = DateTimeFormatter.ofPattern("yyyy/MM/dd");
    private static final int SLIDE_W = 960;
    private static final int SLIDE_H = 540;


    public String export(Long prepId) {
        log.info("导出PPT, prepId={}", prepId);
        try {
            TeachingContent content = teachingContentMapper.selectById(prepId);
            if (content == null) throw new IllegalArgumentException("备课内容不存在: " + prepId);
            return exportFromTeachingContent(content);
        } catch (Exception e) {
            log.error("PPT导出失败, prepId={}", prepId, e);
            throw new RuntimeException("PPT导出失败: " + e.getMessage(), e);
        }
    }

    public String exportFromTeachingContent(TeachingContent content) {
        String json = content.getPptStructure();
        if (json == null || json.isEmpty()) throw new IllegalArgumentException("PPT结构数据为空");
        return exportFromJson(json, content.getTemplateId(), content.getTitle());
    }

    public String exportFromJson(String json, Long templateId, String fileName) {
        try {
            PptStructureDTO structure = objectMapper.readValue(json, PptStructureDTO.class);
            return exportFromStructure(structure, templateId, fileName);
        } catch (Exception e) {
            log.error("解析PPT结构失败", e);
            throw new RuntimeException("解析PPT结构失败: " + e.getMessage(), e);
        }
    }

    public String exportFromStructure(PptStructureDTO structure, Long templateId, String fileName) {
        try {
            byte[] pptxBytes = generatePptxBytes(structure, templateId);
            String objectName = buildObjectName(fileName, structure.getTitle());
            String contentType = "application/vnd.openxmlformats-officedocument.presentationml.presentation";
            String url = uploadToMinio(objectName, pptxBytes, contentType);
            log.info("PPT导出成功, url={}", url);
            return url;
        } catch (Exception e) {
            log.error("PPT导出失败", e);
            throw new RuntimeException("PPT导出失败: " + e.getMessage(), e);
        }
    }

    public byte[] generatePptxBytes(PptStructureDTO structure, Long templateId) {
        try (XMLSlideShow ppt = new XMLSlideShow();
             ByteArrayOutputStream out = new ByteArrayOutputStream()) {

            PptStructureDTO.TemplateConfig cfg = resolveConfig(structure);
            ppt.setPageSize(new Dimension(SLIDE_W, SLIDE_H));

            // ——— 兼容 Python generate-ppt 返回的 {pptId, slides} 顶层格式 ———
            // 如果没传 title/description，从 type=cover 的 slide 里提取
            List<PptStructureDTO.SlideDTO> slides = structure.getSlides();
            if (slides != null && !slides.isEmpty()) {
                PptStructureDTO.SlideDTO cover = slides.stream()
                        .filter(s -> "cover".equalsIgnoreCase(s.getType()))
                        .findFirst()
                        .orElse(null);
                if (cover != null) {
                    if ((structure.getTitle() == null || structure.getTitle().isBlank())
                            && cover.getTitle() != null) {
                        structure.setTitle(cover.getTitle());
                    }
                    if ((structure.getDescription() == null || structure.getDescription().isBlank())
                            && cover.getBulletPoints() != null && !cover.getBulletPoints().isEmpty()) {
                        structure.setDescription(String.join(" · ", cover.getBulletPoints()));
                    }
                }
            }

            buildTitleSlide(ppt, structure, cfg);

            if (slides != null) {
                for (PptStructureDTO.SlideDTO s : slides) {
                    // cover 页已经作为 buildTitleSlide 的标题/副标题来源，跳过避免重复画封面
                    if ("cover".equalsIgnoreCase(s.getType())) continue;
                    buildContentSlide(ppt, s, cfg);
                }
            }

            buildEndSlide(ppt, structure, cfg);

            ppt.write(out);
            log.info("PPTX生成成功, size={}", out.size());
            return out.toByteArray();
        } catch (Exception e) {
            log.error("生成PPTX失败", e);
            throw new RuntimeException("生成PPTX失败: " + e.getMessage(), e);
        }
    }

    @Override
    public String getTempFilePath(Long prepId) {
        String datePath = LocalDateTime.now().format(DATE_FMT);
        return PPT_OBJECT_PREFIX + datePath + "/prep-" + prepId + "-" + UUID.randomUUID().toString().substring(0, 8) + ".pptx";
    }

    private String uploadToMinio(String objectName, byte[] data, String contentType) {
        try (InputStream is = new ByteArrayInputStream(data)) {
            minioClient.putObject(
                    PutObjectArgs.builder()
                            .bucket(bucketName)
                            .object(objectName)
                            .stream(is, -1, 10485760)
                            .contentType(contentType)
                            .build()
            );
            String url = String.format("%s/%s/%s", endpoint, bucketName, objectName);
            log.info("文件上传成功, url={}", url);
            return url;
        } catch (Exception e) {
            log.error("文件上传失败, objectName={}", objectName, e);
            throw new RuntimeException("文件上传失败: " + e.getMessage(), e);
        }
    }

    private PptStructureDTO.TemplateConfig resolveConfig(PptStructureDTO structure) {
        PptStructureDTO.TemplateConfig cfg = structure.getTemplateConfig();
        if (cfg == null) {
            cfg = PptStructureDTO.TemplateConfig.builder()
                    .primaryColor("#546E7A")        // 封面/结束页：深灰蓝色（白字清晰）
                    .secondaryColor("#263238")      // 内容页标题：炭灰色（在浅灰背景上清楚）
                    .backgroundColor("#ECEFF1")     // 中间内容页：浅灰背景（护眼不刺眼）
                    .titleFont("微软雅黑")
                    .contentFont("微软雅黑")
                    .titleFontSize(36)
                    .contentFontSize(20)
                    .build();
        }
        if (cfg.getSecondaryColor() == null || cfg.getSecondaryColor().isEmpty()) {
            cfg.setSecondaryColor("#263238");
        }
        return cfg;
    }

    private void buildTitleSlide(XMLSlideShow ppt, PptStructureDTO s, PptStructureDTO.TemplateConfig cfg) {
        XSLFSlide slide = ppt.createSlide();
        setBg(slide, cfg.getPrimaryColor());

        XSLFTextShape title = slide.createTextBox();
        title.setAnchor(new Rectangle(50, 150, SLIDE_W - 100, 100));
        setText(title, s.getTitle() != null ? s.getTitle() : "演示文稿", 44, Color.WHITE, cfg.getTitleFont(), true);

        XSLFTextShape subtitle = slide.createTextBox();
        subtitle.setAnchor(new Rectangle(50, 280, SLIDE_W - 100, 50));
        setText(subtitle, s.getDescription() != null ? s.getDescription() : "", 20, new Color(149, 165, 166), cfg.getContentFont(), false);
    }

    private void buildContentSlide(XMLSlideShow ppt, PptStructureDTO.SlideDTO slideDto, PptStructureDTO.TemplateConfig cfg) {
        XSLFSlide slide = ppt.createSlide();
        setBg(slide, cfg.getBackgroundColor());

        String type = slideDto.getType() == null ? "content" : slideDto.getType().toLowerCase();

        // ——— 标题：根据 type 加不同图标前缀 ———
        if (slideDto.getTitle() != null && !slideDto.getTitle().isEmpty()) {
            String prefix = switch (type) {
                case "summary"     -> "📌 ";
                case "homework"    -> "📝 ";
                case "interactive" -> "❓ ";
                default            -> "";
            };
            XSLFTextShape title = slide.createTextBox();
            title.setAnchor(new Rectangle(50, 30, SLIDE_W - 100, 60));
            setText(title, prefix + slideDto.getTitle(), cfg.getTitleFontSize(),
                    parseColor(cfg.getSecondaryColor()), cfg.getTitleFont(), true);
        }

        // ——— 正文：要点列表 或 纯正文 ———
        StringBuilder sb = new StringBuilder();
        if (slideDto.getBulletPoints() != null && !slideDto.getBulletPoints().isEmpty()) {
            for (int i = 0; i < slideDto.getBulletPoints().size(); i++) {
                if (i > 0) sb.append("\n");
                sb.append("• ").append(slideDto.getBulletPoints().get(i));
            }
        } else if (slideDto.getContent() != null && !slideDto.getContent().isEmpty()) {
            sb.append(slideDto.getContent());
        }

        // ——— 公式：有就追加一行，用粗体稍大号字显示 ———
        if (slideDto.getFormula() != null && !slideDto.getFormula().isEmpty()) {
            if (sb.length() > 0) sb.append("\n\n");
            sb.append("✏️ 公式：").append(slideDto.getFormula());
        }

        // ——— 互动题：type=interactive 且 interaction 不为 null 时追加 ———
        PptStructureDTO.InteractionDTO inter = slideDto.getInteraction();
        if ("interactive".equals(type) && inter != null) {
            if (inter.getQuestion() != null && !inter.getQuestion().isEmpty()) {
                if (sb.length() > 0) sb.append("\n\n");
                sb.append("【提问】").append(inter.getQuestion());
            }
            if (inter.getOptions() != null && !inter.getOptions().isEmpty()) {
                sb.append("\n");
                for (String opt : inter.getOptions()) {
                    sb.append("\n  ○ ").append(opt);
                }
            }
            if (inter.getAnswer() != null && !inter.getAnswer().isEmpty()) {
                sb.append("\n\n✅ 参考答案：").append(inter.getAnswer());
            }
        }

        // ——— 高亮关键词：最后一行橙色显示 ———
        if (slideDto.getHighlightPoints() != null && !slideDto.getHighlightPoints().isEmpty()) {
            if (sb.length() > 0) sb.append("\n\n");
            sb.append("✨关键词：").append(String.join("、", slideDto.getHighlightPoints()));
        }

        XSLFTextShape content = slide.createTextBox();
        content.setAnchor(new Rectangle(50, 120, SLIDE_W - 100, SLIDE_H - 180));
        // 关键词用橙色单独写（这里简单实现：统一用正文字色写，后续可按段落拆）
        setText(content, sb.toString(), cfg.getContentFontSize(), Color.BLACK, cfg.getContentFont(), false);
    }

    private void buildEndSlide(XMLSlideShow ppt, PptStructureDTO s, PptStructureDTO.TemplateConfig cfg) {
        XSLFSlide slide = ppt.createSlide();
        setBg(slide, cfg.getPrimaryColor());

        XSLFTextShape thank = slide.createTextBox();
        thank.setAnchor(new Rectangle(180, 180, 600, 100));
        setText(thank, "谢谢观看", 56, Color.WHITE, cfg.getTitleFont(), true);

        XSLFTextShape sub = slide.createTextBox();
        sub.setAnchor(new Rectangle(180, 300, 600, 50));
        setText(sub, s.getTitle() != null ? s.getTitle() : "", 24, new Color(200, 200, 200), cfg.getContentFont(), false);
    }

    private void setText(XSLFTextShape shape, String text, Integer fontSize, Color color, String font, boolean bold) {
        int size = fontSize != null ? fontSize : 20;
        shape.setText(text != null ? text : "");
        XSLFTextRun run = shape.getTextParagraphs().get(0).getTextRuns().get(0);
        run.setFontSize((double) size);
        run.setFontColor(color);
        run.setFontFamily(font);
        run.setBold(bold);
    }

    private void setBg(XSLFSlide slide, String colorHex) {
        try {
            slide.getBackground().setFillColor(parseColor(colorHex));
        } catch (Exception e) {
            log.warn("设置背景色失败: {}", colorHex);
        }
    }

    private Color parseColor(String hex) {
        if (hex == null || hex.isEmpty()) return Color.BLACK;
        try {
            String h = hex.replace("#", "");
            if (h.length() == 6) {
                return new Color(
                        Integer.parseInt(h.substring(0, 2), 16),
                        Integer.parseInt(h.substring(2, 4), 16),
                        Integer.parseInt(h.substring(4, 6), 16));
            }
        } catch (Exception ignored) {}
        return Color.BLACK;
    }

    private String buildObjectName(String fileName, String title) {
        String datePath = LocalDateTime.now().format(DATE_FMT);
        String name = fileName != null ? fileName : (title != null ? title : "presentation");
        return PPT_OBJECT_PREFIX + datePath + "/" + name + "-" + UUID.randomUUID().toString().substring(0, 8) + ".pptx";
    }
}
