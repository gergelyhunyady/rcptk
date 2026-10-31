/**
 * Cloudflare Worker: Telegram to GitHub Actions Relay
 *
 * Environment variables needed in Cloudflare Dashboard:
 * - ALLOWED_USER_ID: Your Telegram numeric user ID (e.g. "123456789")
 * - GITHUB_PAT: GitHub Personal Access Token (with "Contents: Read & Write" on rcptk)
 * - TELEGRAM_SECRET: Random secret string to verify Telegram webhook calls
 * - GITHUB_REPO: "gergelyhunyady/rcptk" (default)
 * - TELEGRAM_BOT_TOKEN: Your Telegram bot token (for fast feedback / error notices)
 */

export default {
  async fetch(request, env) {
    if (request.method !== "POST") {
      return new Response("Method not allowed", { status: 405 });
    }

    // 1. Verify Telegram secret token header
    const secretHeader = request.headers.get("X-Telegram-Bot-Api-Secret-Token");
    if (env.TELEGRAM_SECRET && secretHeader !== env.TELEGRAM_SECRET) {
      return new Response("Unauthorized", { status: 401 });
    }

    let update;
    try {
      update = await request.json();
    } catch {
      return new Response("Bad Request", { status: 400 });
    }

    const message = update.message || update.edited_message;
    if (!message) {
      return new Response("OK", { status: 200 });
    }

    // 2. Single-user security check: only accept messages from you
    const senderId = message.from?.id;
    if (String(senderId) !== String(env.ALLOWED_USER_ID)) {
      console.warn(`Ignored message from unauthorized user: ${senderId}`);
      return new Response("OK", { status: 200 });
    }

    const chatId = message.chat?.id;
    const messageId = message.message_id;
    const text = message.text || "";
    const caption = message.caption || "";
    const combinedText = text || caption;

    // Collect photo file IDs (best resolution) or document images
    const photoFileIds = [];
    if (message.photo && message.photo.length > 0) {
      const bestPhoto = message.photo[message.photo.length - 1];
      photoFileIds.push(bestPhoto.file_id);
    } else if (message.document && message.document.mime_type?.startsWith("image/")) {
      photoFileIds.push(message.document.file_id);
    }

    if (!combinedText && photoFileIds.length === 0) {
      return new Response("OK", { status: 200 });
    }

    // 3. Send immediate "typing..." action so Telegram UI shows activity
    if (env.TELEGRAM_BOT_TOKEN && chatId) {
      fetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/sendChatAction`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ chat_id: chatId, action: "typing" }),
      }).catch(() => {});
    }

    // 4. Trigger GitHub Actions repository_dispatch
    const repo = env.GITHUB_REPO || "gergelyhunyady/rcptk";
    const dispatchUrl = `https://api.github.com/repos/${repo}/dispatches`;

    const payload = {
      event_type: "new-recipe",
      client_payload: {
        chat_id: chatId,
        message_id: messageId,
        text: text,
        caption: caption,
        photo_file_ids: photoFileIds,
      },
    };

    const ghResp = await fetch(dispatchUrl, {
      method: "POST",
      headers: {
        "Accept": "application/vnd.github.v3+json",
        "Authorization": `Bearer ${env.GITHUB_PAT}`,
        "Content-Type": "application/json",
        "User-Agent": "rcptk-telegram-relay",
      },
      body: JSON.stringify(payload),
    });

    if (!ghResp.ok) {
      const errText = await ghResp.text();
      console.error(`GitHub dispatch failed: HTTP ${ghResp.status} - ${errText}`);
      if (env.TELEGRAM_BOT_TOKEN && chatId) {
        await fetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/sendMessage`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            chat_id: chatId,
            text: `❌ Nem sikerült elindítani a GitHub Actiont: HTTP ${ghResp.status}`,
          }),
        });
      }
      return new Response("GitHub dispatch failed", { status: 500 });
    }

    return new Response("OK", { status: 200 });
  },
};
