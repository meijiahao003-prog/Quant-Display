/** Python-only features have an explicit boundary on the hosted quote service. */
export function GET() {
  return Response.json({ detail: "此功能需要连接完整 Python 服务；线上行情服务不保存训练样本或执行模型训练。" }, { status: 501 });
}
export const POST = GET;
