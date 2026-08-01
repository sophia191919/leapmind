package com.treepeople.leapmindtts.service;

/**
 * PPT导出服务接口
 */

public interface PptxExportService {

    /**
     * 导出备课内容为PPTX文件
     *
     * @param prepId 备课ID
     * @return 下载URL
     */
    String export(Long prepId);

    /**
     * 获取临时文件路径
     *
     * @param prepId 备课ID
     * @return 临时文件路径
     */
    String getTempFilePath(Long prepId);
}
