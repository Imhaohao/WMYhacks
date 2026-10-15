// Same-origin proxy to the voice worker's onboarding API, so preview URLs need
// no CORS entry and the ngrok browser interstitial never reaches fetch().
const TARGET = (process.env.ONBOARDING_API_TARGET_ORIGIN || "").replace(/\/$/, "")
const FORWARDED_HEADERS = ["content-type", "x-onboarding-token", "accept"]

function readBody(req) {
  if (req.method === "GET" || req.method === "HEAD") return Promise.resolve(undefined)
  return new Promise((resolve, reject) => {
    const chunks = []
    req.on("data", (chunk) => chunks.push(Buffer.from(chunk)))
    req.on("end", () => resolve(Buffer.concat(chunks)))
    req.on("error", reject)
  })
}

function targetUrl(req) {
  const url = new URL(req.url, "http://proxy.local")
  const path = url.searchParams.get("path") || ""
  url.searchParams.delete("path")
  const target = new URL(`/api/${path}`, TARGET)
  target.search = url.searchParams.toString()
  return target
}

export default async function handler(req, res) {
  if (!TARGET) {
    res.statusCode = 503
    res.setHeader("content-type", "application/json")
    res.end(JSON.stringify({ detail: "Gotchu's API is not configured for this deployment." }))
    return
  }
  const headers = { "ngrok-skip-browser-warning": "true" }
  for (const name of FORWARDED_HEADERS) {
    if (req.headers[name]) headers[name] = String(req.headers[name])
  }
  try {
    const upstream = await fetch(targetUrl(req), { method: req.method, headers, body: await readBody(req) })
    res.statusCode = upstream.status
    res.setHeader("content-type", upstream.headers.get("content-type") || "application/json")
    res.setHeader("cache-control", "no-store")
    res.end(Buffer.from(await upstream.arrayBuffer()))
  } catch {
    res.statusCode = 502
    res.setHeader("content-type", "application/json")
    res.end(JSON.stringify({ detail: "Gotchu's API did not answer." }))
  }
}
