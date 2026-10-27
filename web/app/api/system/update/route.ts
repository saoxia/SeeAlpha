// Retired URLs must not fall through to the legacy backend proxy.
function removed() {
  return Response.json(
    { detail: "旧版系统更新功能已移除。" },
    { status: 410, headers: { "Cache-Control": "no-store" } },
  );
}

export { removed as GET, removed as POST, removed as PUT, removed as PATCH, removed as DELETE };
