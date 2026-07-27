 package com.treepeople.leapmindtts.controller.lesson;
 
 import com.treepeople.leapmindtts.pojo.result.ApiResponse;
 import com.treepeople.leapmindtts.pojo.vo.EducationStageVO;
 import com.treepeople.leapmindtts.pojo.vo.GradeVO;
 import com.treepeople.leapmindtts.service.lesson.EducationStageService;
 import jakarta.validation.constraints.Pattern;
 import lombok.RequiredArgsConstructor;
 import org.springframework.validation.annotation.Validated;
 import org.springframework.web.bind.annotation.*;
 
 import java.util.List;
 
 @RestController
 @RequestMapping("/api/education")
 @RequiredArgsConstructor
 @Validated // 开启方法级别的参数校验
 public class CourseController {
 
     private final EducationStageService educationStageService;
 
     @GetMapping("/stages")
     public ApiResponse<List<EducationStageVO>> getAllStages() {
         List<EducationStageVO> stages = educationStageService.getAllStages();
         return ApiResponse.success(stages);
     }
 
     @GetMapping("/stages/{stageCode}/grades")
     public ApiResponse<List<GradeVO>> getGradesByStage(
             @PathVariable("stageCode") // 1. 显式指定参数名，防 Spring Boot 3.2+ 报错
             @Pattern(regexp = "^[a-zA-Z0-9_]{2,20}$", message = "阶段代码格式不正确") // 2. 兼容小写字母
             String stageCode) {
         // 建议：如果数据库存的是大写，可以这里统一转大写
         List<GradeVO> grades = educationStageService.getGradesByStage(stageCode.toUpperCase());
         return ApiResponse.success(grades);
     }
 }