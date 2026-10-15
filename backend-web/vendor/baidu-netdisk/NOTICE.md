# 百度网盘接口适配来源

接口定义、公开体验 AppKey 和上传流程参考：
https://github.com/baidu-netdisk/mcp

核对版本：`b3983d330fea79c7b72e6b7014803e1830148d2c`。

本项目使用已有 httpx 适配开放平台接口，使用官方 MCP Python SDK
连接百度 SSE 服务，没有复制上游整个生成的 OpenAPI SDK。
上传流程根据上游示例适配为异步、用户隔离且不自动覆盖文件。

上游许可证保留在 LICENSE；MCP Python SDK 亦为 MIT。
