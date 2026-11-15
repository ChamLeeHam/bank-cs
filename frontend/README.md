# React 客服界面

从项目根目录安装前端依赖并构建：

```powershell
cd frontend
npm ci
npm run build
cd ..
uv run uvicorn src.api:app --host 127.0.0.1 --port 8002
```

浏览器打开 `http://127.0.0.1:8002/` 进入客户聊天，打开 `http://127.0.0.1:8002/agent` 进入坐席工作台。
FastAPI 从 `src/web/react` 提供构建产物。修改 React 源码后重新构建并刷新页面。未构建时，FastAPI 回退到原来的静态页面。

前端开发时，先在 8002 启动后端，再在 `frontend` 运行 `npm run dev`。Vite 的客户入口为 `/`，坐席入口为 `/agent.html`，两个页面分别构建，`/api` 请求转发给本地 FastAPI。
