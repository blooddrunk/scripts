# LightLayer User API 文档存档

本目录长期保存 LightLayer 官方 User API 文档的快照，供脚本维护时离线核对。
官方页面若更新，可按下方方法重新抓取并存为新日期的版本。

## 版本信息

- **版本日期（抓取时间）**: 2026-11-10 17:13 (UTC+8)
- **来源页面**: <https://account.lightlayer.net/index.php?/userapi#introduction>
- **Swagger 规范来源**: <https://account.lightlayer.net/?cmd=userapi&action=swagger>

| 文件 | 说明 | SHA256 |
|---|---|---|
| `userapi-2026-11-10.md` | 文档页面的 Markdown 转换版（便于阅读/搜索），由 `uvx --from markitdown markitdown` 从 HTML 转换 | `022d9e1bf8d2f3018b71022cd417dc1893b1ca72703d88306e2f2ade988e1634` |
| `userapi-2026-11-10.html` | 文档页面原始 HTML（Slate 静态页） | `13465528b258874e1ce8c4b8c81af78560560deedc980852e01d315e15e95ecf` |
| `userapi-swagger-2026-11-10.json` | Swagger 2.0 机器可读规范（71 个路径，最权威） | `8ddef03e27e44a32c4f6bcee5609dbbdfc6de3d9f9d0726f8ae2c053a838e736` |

## 该版本快照下的实测备注（2026-11-10）

- 文档本身未提供任何验证码参数；`POST /login` 的账号密码格式与脚本实现一致。
- 站点 SafeLine（雷池）WAF 按路径硬拦截 `POST /api/login` 与 `GET /api/details`
  （403 HTML，无论请求头/方法/请求体），其余端点正常；
  脚本因此在 `/login` 被 403 拦截时自动降级为文档同样支持的 HTTP Basic 认证。
- Basic Auth 实测可用：`/balance` `/payment` `/invoice` `/category` `/order/{id}`
  `/quote` 均返回正常业务数据。

## 更新方法

```bash
D=docs/userapi-$(date +%F)
curl -sL -A "Mozilla/5.0" 'https://account.lightlayer.net/index.php?/userapi' -o "$D.html"
curl -sL -A "Mozilla/5.0" 'https://account.lightlayer.net/?cmd=userapi&action=swagger' -o "userapi-swagger-$(date +%F).json"
uvx --from markitdown markitdown "$D.html" -o "$D.md"
sha256sum "$D".* userapi-swagger-*.json   # 更新本文件中的表格
```
