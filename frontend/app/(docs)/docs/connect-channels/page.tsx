import type { Metadata } from "next";
import Link from "next/link";
import { site } from "@/lib/site";

export const metadata: Metadata = {
	title: "Connect Facebook, Instagram and WhatsApp",
	description: `Step-by-step guide to connect your Facebook Page, Instagram account and WhatsApp number to ${site.productName}, with fixes for common problems.`,
};

// Facts here were checked against Meta's developer docs on 2026-11-12. Meta renames menu items often: if a step stops matching, fix it here.
const META = {
	messengerWebhooks: "https://developers.facebook.com/documentation/business-messaging/messenger-platform/webhooks",
	instagramStart: "https://developers.facebook.com/documentation/business-messaging/instagram-messaging/get-started",
	whatsappStart: "https://developers.facebook.com/documentation/business-messaging/whatsapp/get-started",
	whatsappRegister: "https://developers.facebook.com/documentation/business-messaging/whatsapp/business-phone-numbers/registration",
	apps: "https://developers.facebook.com/apps/",
	explorer: "https://developers.facebook.com/tools/explorer/",
	businessSettings: "https://business.facebook.com/settings/",
};

function Callout({ title, children }: { title: string; children: React.ReactNode }) {
	return (
		<div className="mt-6 rounded-md border border-border bg-muted/50 p-4">
			<p className="!mt-0 font-semibold text-foreground">{title}</p>
			{children}
		</div>
	);
}

export default function ConnectChannelsPage() {
	const mail = `mailto:${site.supportEmail}?subject=${encodeURIComponent("Help connecting a channel")}`;
	return (
		<>
			<h1>Connect Facebook, Instagram and WhatsApp</h1>
			<p>
				This guide shows how to connect your Facebook Page, Instagram account or WhatsApp number to {site.productName}, so
				your agent can answer your customers there. Allow about 20 to 30 minutes for your first channel. If something goes
				wrong, jump to <a href="#troubleshooting">Troubleshooting</a>.
			</p>

			<h2 id="how-it-works">How it works</h2>
			<ol>
				<li>
					You use a <strong>Meta app</strong> (a free developer app from Meta, the company behind Facebook, Instagram and
					WhatsApp). It is the doorway between Meta and {site.productName}.
				</li>
				<li>
					You copy a few values from Meta (IDs, a secret and an access token) into {site.productName} under{" "}
					<strong>Channels</strong>.
				</li>
				<li>
					You copy two values from {site.productName} (the <strong>Callback URL</strong> and the{" "}
					<strong>Verify token</strong>) back into Meta, so Meta knows where to send your customers&apos; messages.
				</li>
			</ol>

			<Callout title="Read this first: who can message you">
				<ul>
					<li>
						<strong>Facebook and Instagram:</strong> until Meta approves your app (called <em>App Review</em>, which
						gives it &ldquo;Advanced Access&rdquo;), your Page only receives messages from people who have a role on
						your app: Administrator, Developer or Tester. That is fine for testing. To serve real customers you need
						that approval. You start it in your app under <strong>App Review</strong>.
					</li>
					<li>
						<strong>WhatsApp:</strong> the free test number only sends to up to 5 phone numbers you have verified. For
						real customers, add and register your own business number (see{" "}
						<a href="#whatsapp-own-number">Using your own number</a>).
					</li>
				</ul>
			</Callout>

			<h2 id="contents">Choose your channel</h2>
			<ul>
				<li><a href="#before-you-start">Before you start (all channels)</a></li>
				<li><a href="#facebook">Facebook Messenger</a></li>
				<li><a href="#instagram">Instagram</a></li>
				<li><a href="#whatsapp">WhatsApp</a></li>
				<li><a href="#troubleshooting">Troubleshooting</a></li>
			</ul>

			<h2 id="before-you-start">Before you start (all channels)</h2>
			<p>You need:</p>
			<ul>
				<li>A {site.productName} account with at least one <strong>agent memory</strong> set up. Each channel must be linked to one, or it will not reply.</li>
				<li>A personal Facebook account that is an <strong>admin</strong> of your Facebook Page (and, for WhatsApp, of your Meta business portfolio).</li>
				<li>A free Meta developer account: sign in at <a href={META.apps} target="_blank" rel="noreferrer">developers.facebook.com</a> with that Facebook account.</li>
			</ul>

			<h3 id="meta-app">Create your Meta app and find its ID and secret</h3>
			<ol>
				<li>Open <a href={META.apps} target="_blank" rel="noreferrer">developers.facebook.com/apps</a> and click <strong>Create app</strong>. Follow the prompts and choose the use case that mentions messaging for your channel.</li>
				<li>Open your app, then go to <strong>App settings → Basic</strong>.</li>
				<li>Copy the <strong>App ID</strong>. Click <strong>Show</strong> next to <strong>App secret</strong> and copy it too.</li>
			</ol>
			<p>
				Keep the App secret private, like a password. Never post it in a chat, a screenshot or an email, including to us.
				You will paste the App ID and App secret into every channel you connect.
			</p>

			<h3 id="verify-token">What is the Verify token?</h3>
			<p>
				It is a password you make up. Meta sends it back to {site.productName} to check the connection is yours. In the
				channel form click the <strong>refresh</strong> button next to <strong>Verify token</strong> to generate one. You paste
				the same value into Meta later.
			</p>

			<h3 id="callback-url">Where do I find the Callback URL?</h3>
			<p>
				In {site.productName} go to <strong>Channels → Add Channel</strong>. Under <strong>Webhook setup</strong> you will see the{" "}
				<strong>Callback URL</strong> with a copy button. It is the same for every channel.
			</p>

			<Callout title="Order matters">
				<p>
					Save the channel in {site.productName} <strong>before</strong> you click Save in Meta&apos;s Webhooks screen. When
					you save there, Meta checks your Verify token immediately, and {site.productName} can only answer once the channel
					exists.
				</p>
			</Callout>

			{/* ---------------- Facebook ---------------- */}
			<h2 id="facebook">Facebook Messenger</h2>
			<p>
				Lets your agent answer messages sent to your Facebook Page.
			</p>

			<h3>What you need to copy into {site.productName}</h3>
			<table>
				<thead>
					<tr><th>Field in {site.productName}</th><th>Where to find it</th></tr>
				</thead>
				<tbody>
					<tr><td>Page ID</td><td>Your Facebook Page → <strong>About</strong> (or <strong>Settings → Page setup / Page info</strong>), at the bottom. It is a long number.</td></tr>
					<tr><td>Page Access Token</td><td>Meta app → <strong>Messenger → Settings → Access tokens</strong> (Token Generation), step 3 below.</td></tr>
					<tr><td>App ID, App Secret</td><td>Meta app → <strong>App settings → Basic</strong>.</td></tr>
					<tr><td>Verify Token</td><td>You make it up. Use the refresh button in {site.productName}.</td></tr>
				</tbody>
			</table>

			<h3>Steps</h3>
			<ol>
				<li>In your Meta app, add the <strong>Messenger</strong> product (use <strong>Add product</strong> or the use-case list on the app dashboard).</li>
				<li>Open <strong>Messenger → Settings</strong> and find the token section (Meta calls it <strong>Token Generation</strong> or <strong>Access tokens</strong>). Pick your Page.</li>
				<li>Copy the Page access token (click <strong>Generate token</strong> if you see that button). Meta does not save it: copy it now. Each new token leaves older ones working.</li>
				<li>
					In {site.productName}: <strong>Channels → Add Channel → Facebook</strong>. Fill in the fields from the table, pick your
					agent memory, create a Verify token, and click <strong>Save</strong>.
				</li>
				<li>
					Back in Meta, on <strong>Messenger → Settings → Webhooks</strong>, click <strong>Configure webhooks</strong>. Paste the{" "}
					<strong>Callback URL</strong> and <strong>Verify token</strong>, then click <strong>Verify and save</strong>.
				</li>
				<li>
					In the same screen, subscribe to the <code>messages</code> field (and <code>messaging_postbacks</code> if listed).
				</li>
				<li>
					<strong>Subscribe your Page to your app.</strong> This step is easy to miss: without it Meta looks fine but sends nothing.
					<ol>
						<li>Open the <a href={META.explorer} target="_blank" rel="noreferrer">Graph API Explorer</a> and pick your app.</li>
						<li>Click <strong>Get Token → Get User Access Token</strong>, tick <code>pages_manage_metadata</code> and <code>pages_messaging</code>, and confirm.</li>
						<li>Click <strong>Get Token</strong> again and select your Page. This turns the token into a Page token.</li>
						<li>Change <strong>GET</strong> to <strong>POST</strong>, and in the address box type <code>YOUR_PAGE_ID/subscribed_apps?subscribed_fields=messages</code>, then click <strong>Submit</strong>.</li>
						<li>You should see <code>{`{"success": true}`}</code>.</li>
					</ol>
				</li>
				<li>
					<strong>Test it.</strong> From a Facebook account that has a role on your app (see the box at the top), send a message to your Page. Your agent should
					reply within a few seconds.
				</li>
			</ol>
			<p>
				Meta&apos;s own guides: <a href={META.messengerWebhooks} target="_blank" rel="noreferrer">Messenger webhooks</a>.
			</p>

			{/* ---------------- Instagram ---------------- */}
			<h2 id="instagram">Instagram</h2>
			<p>
				Lets your agent answer Instagram direct messages. It runs through a Facebook Page, so set up the Facebook Page part first.
			</p>

			<h3>What you need first</h3>
			<ul>
				<li>An Instagram <strong>Professional</strong> account (Business or Creator). Switch it in the Instagram app&apos;s settings, under account type.</li>
				<li>That account <strong>linked to a Facebook Page</strong> you admin.</li>
				<li>
					Message access turned on: in Instagram go to <strong>Settings → Messages and story replies → Message controls → Connected tools</strong> and switch on{" "}
					<strong>Allow access to messages</strong>. Without this, no messages arrive.
				</li>
				<li>Your Meta app with the <strong>Messenger</strong> product added.</li>
			</ul>

			<h3>What you need to copy into {site.productName}</h3>
			<table>
				<thead>
					<tr><th>Field in {site.productName}</th><th>Where to find it</th></tr>
				</thead>
				<tbody>
					<tr><td>Instagram Account ID</td><td>Graph API Explorer: run <code>GET YOUR_PAGE_ID?fields=instagram_business_account</code>. The <code>id</code> inside the result is your Instagram Account ID.</td></tr>
					<tr><td>Facebook Page ID</td><td>The Page linked to your Instagram account (see the Facebook section).</td></tr>
					<tr><td>Page Access Token</td><td>Meta app → <strong>Messenger → Instagram settings → Access tokens</strong> (Generate token). If you do not see it, use the Graph API Explorer: choose your app, click <strong>Get Token → Get User Access Token</strong>, tick <code>instagram_basic</code>, <code>instagram_manage_messages</code> and <code>pages_manage_metadata</code>, then run <code>GET me/accounts</code> and copy your Page&apos;s <code>access_token</code>.</td></tr>
					<tr><td>App ID, App Secret</td><td>Meta app → <strong>App settings → Basic</strong>.</td></tr>
					<tr><td>Verify Token</td><td>You make it up. Use the refresh button in {site.productName}.</td></tr>
				</tbody>
			</table>

			<h3>Steps</h3>
			<ol>
				<li>Collect the values above.</li>
				<li>
					In {site.productName}: <strong>Channels → Add Channel → Instagram</strong>. Fill in the fields, pick your agent memory,
					create a Verify token, and click <strong>Save</strong>.
				</li>
				<li>
					In Meta open <strong>Messenger → Instagram settings → Webhooks</strong>. Paste the <strong>Callback URL</strong> and{" "}
					<strong>Verify token</strong>, click <strong>Verify and save</strong>, then subscribe to <code>messages</code>.
				</li>
				<li>
					<a href="#facebook">Subscribe your Page to your app</a> (step 7 in the Facebook section) if you have not already. Instagram uses the same Page subscription.
				</li>
				<li>
					<strong>Test it.</strong> From an Instagram account that has a role on your Meta app, send a direct message to your business account. If the sender does not follow you,
					the message lands in <strong>Requests</strong> in your Instagram inbox. Your agent still replies.
				</li>
			</ol>
			<p>
				Meta&apos;s own guide: <a href={META.instagramStart} target="_blank" rel="noreferrer">Get started with Instagram Messaging</a>.
			</p>

			{/* ---------------- WhatsApp ---------------- */}
			<h2 id="whatsapp">WhatsApp</h2>
			<p>
				Lets your agent answer WhatsApp messages sent to a business number. WhatsApp uses its own token and IDs, different from Facebook and Instagram.
			</p>

			<h3>What you need first</h3>
			<ul>
				<li>A Meta <strong>business portfolio</strong> (Meta Business Suite) and a <strong>WhatsApp Business Account</strong>. Adding the WhatsApp product to your Meta app creates these for you if you do not have them.</li>
				<li>A phone number. For a first try, Meta gives you a free <strong>test number</strong>.</li>
			</ul>

			<h3>What you need to copy into {site.productName}</h3>
			<table>
				<thead>
					<tr><th>Field in {site.productName}</th><th>Where to find it</th></tr>
				</thead>
				<tbody>
					<tr><td>Phone Number ID</td><td>Meta app → <strong>WhatsApp → API Setup</strong>, under the phone number you send from. It is a number, not the phone number itself.</td></tr>
					<tr><td>Business Account ID</td><td>The same screen: <strong>WhatsApp Business Account ID</strong> (also called WABA ID).</td></tr>
					<tr><td>Access Token</td><td>A <strong>permanent</strong> token from a system user (steps below). Do not use the temporary token on the API Setup page: it expires in 24 hours.</td></tr>
					<tr><td>App ID, App Secret</td><td>Meta app → <strong>App settings → Basic</strong>. Use the app that owns the WhatsApp product.</td></tr>
					<tr><td>Verify Token</td><td>You make it up. Use the refresh button in {site.productName}.</td></tr>
				</tbody>
			</table>

			<h3>Steps</h3>
			<ol>
				<li>In your Meta app add the <strong>WhatsApp</strong> product, then open <strong>WhatsApp → API Setup</strong>. Note the Phone Number ID and Business Account ID, and add the phone numbers you want to test with as recipients (up to 5).</li>
				<li>
					<strong>Create a permanent token.</strong> Open <a href={META.businessSettings} target="_blank" rel="noreferrer">Business Settings</a> and go to <strong>Users → System users</strong>.
					<ol>
						<li>Click <strong>Add</strong> and create a system user.</li>
						<li>Select it and click <strong>Assign assets</strong>. Under apps, choose your app and switch on <strong>Manage app</strong> (Full control). Under WhatsApp accounts, choose your account and switch on <strong>Manage WhatsApp Business accounts</strong> (Full control).</li>
						<li>Click <strong>Generate token</strong>, choose your app, and tick <code>whatsapp_business_messaging</code>, <code>whatsapp_business_management</code> and <code>business_management</code>.</li>
						<li>Copy the token and store it safely. You cannot view it again.</li>
					</ol>
				</li>
				<li>
					In {site.productName}: <strong>Channels → Add Channel → WhatsApp</strong>. Fill in the fields, pick your agent memory,
					create a Verify token, and click <strong>Save</strong>.
				</li>
				<li>
					In Meta open <strong>WhatsApp → Configuration → Webhook</strong>. Paste the <strong>Callback URL</strong> and{" "}
					<strong>Verify token</strong> and click <strong>Verify and save</strong>. Then subscribe to the <code>messages</code> field.
				</li>
				<li>
					<strong>Test it.</strong> From a phone number you added as a recipient, send a WhatsApp message to the test number. Your agent should reply.
				</li>
			</ol>

			<h3 id="whatsapp-own-number">Using your own number</h3>
			<ol>
				<li>In <strong>WhatsApp Manager</strong>, add your business phone number and verify it by the code Meta sends.</li>
				<li>
					Register it. You can only do this through Meta&apos;s API, not WhatsApp Manager or the dashboard: send <code>POST /PHONE_NUMBER_ID/register</code> with
					your access token, <code>&quot;messaging_product&quot;: &quot;whatsapp&quot;</code> and a 6-digit <code>pin</code> you choose. Meta documents the exact request in{" "}
					<a href={META.whatsappRegister} target="_blank" rel="noreferrer">Register a business phone number</a>.
				</li>
				<li>Use the new number&apos;s Phone Number ID in {site.productName}.</li>
			</ol>

			<h3>Good to know about WhatsApp</h3>
			<ul>
				<li>Your agent replies to <strong>text</strong> messages. Photos, voice notes and other types are saved but not answered automatically.</li>
				<li>WhatsApp only allows free-form replies within <strong>24 hours</strong> of the customer&apos;s last message. After that, wait for them to write again.</li>
			</ul>
			<p>
				Meta&apos;s own guide: <a href={META.whatsappStart} target="_blank" rel="noreferrer">WhatsApp Cloud API: Get started</a>.
			</p>

			{/* ---------------- Troubleshooting ---------------- */}
			<h2 id="troubleshooting">Troubleshooting</h2>
			<p>Find what you see in the left column. Work from the top: the first rows are the most common.</p>

			<h3>Setting up the webhook</h3>
			<table>
				<thead>
					<tr><th>If you see</th><th>Do this</th></tr>
				</thead>
				<tbody>
					<tr>
						<td>Meta says <em>&ldquo;The callback URL or verify token couldn&apos;t be validated&rdquo;</em></td>
						<td>
							1) Save the channel in {site.productName} first. 2) Make sure the Verify token in Meta is exactly the one in {site.productName}, with no spaces at the start or end.
							3) Copy the Callback URL again with its copy button.
						</td>
					</tr>
					<tr>
						<td>Meta says verified, but no message ever appears in {site.productName}</td>
						<td>
							Your Page is probably not subscribed to your app. Redo step 7 in the Facebook section. To check, run <code>GET YOUR_PAGE_ID/subscribed_apps</code> in the Graph API Explorer:
							an empty <code>data</code> list means it is not subscribed.
						</td>
					</tr>
					<tr>
						<td>Messages from you arrive, but customers&apos; messages do not (Facebook or Instagram)</td>
						<td>
							Your app does not have Advanced Access yet, so only people with a role on it are delivered. Add the sender as a Tester on the <strong>Roles</strong> tab of your app settings, or complete App Review.
						</td>
					</tr>
					<tr>
						<td>The webhook worked, then stopped</td>
						<td>
							Check the token first (next section). Then confirm you still have a role on the Page and the app, and that you did not change the Verify token on one side only.
						</td>
					</tr>
					<tr>
						<td>Meta shows delivery errors, or messages are refused</td>
						<td>
							The App secret is probably wrong, or from a different app. Copy it again from <strong>App settings → Basic</strong> in the app that holds your webhook, and update the channel.
						</td>
					</tr>
				</tbody>
			</table>

			<h3>Messages arrive but the agent does not reply</h3>
			<table>
				<thead>
					<tr><th>If you see</th><th>Do this</th></tr>
				</thead>
				<tbody>
					<tr>
						<td>The message shows in Conversations, but no reply</td>
						<td>
							Open the channel and check that an <strong>agent memory</strong> is selected. Check you have credits on the <Link href="/credits">Credits</Link> page.
						</td>
					</tr>
					<tr>
						<td>Reply fails, or &ldquo;token invalid or expired&rdquo;</td>
						<td>
							Generate a new token and paste it into the channel. Facebook and Instagram Page tokens stop working if you lose your role on the Page or remove the app. A WhatsApp temporary token expires after 24 hours: use a system user token.
						</td>
					</tr>
					<tr>
						<td>Only photos or stickers were sent</td>
						<td>Expected. Messages with only attachments are saved but not answered automatically.</td>
					</tr>
				</tbody>
			</table>

			<h3>Facebook and Instagram</h3>
			<table>
				<thead>
					<tr><th>If you see</th><th>Do this</th></tr>
				</thead>
				<tbody>
					<tr>
						<td>Instagram: no messages arrive at all</td>
						<td>
							Check <strong>Allow access to messages</strong> is on (Instagram → Settings → Messages and story replies → Message controls → Connected tools). Check the account is Professional and linked to your Page.
						</td>
					</tr>
					<tr>
						<td>Instagram error <code>36103</code></td>
						<td>Messaging is not available for that account yet. Confirm it is a Business or Creator account linked to your Facebook Page.</td>
					</tr>
					<tr>
						<td>Instagram Creator account: webhook never fires</td>
						<td>
							Meta requires one API request first: in the Graph API Explorer run <code>GET YOUR_PAGE_ID/conversations?platform=instagram</code> with your Page token.
						</td>
					</tr>
					<tr>
						<td>Instagram: the message is in <strong>Requests</strong></td>
						<td>That is normal for people who do not follow you. Your agent can still reply.</td>
					</tr>
				</tbody>
			</table>

			<h3>WhatsApp</h3>
			<table>
				<thead>
					<tr><th>If you see</th><th>Do this</th></tr>
				</thead>
				<tbody>
					<tr>
						<td>Error <code>131030</code>, &ldquo;Recipient phone number not in allowed list&rdquo;</td>
						<td>You are on the test number. Add the phone as a recipient in <strong>WhatsApp → API Setup</strong>, or move to your own registered number.</td>
					</tr>
					<tr>
						<td>Error <code>131047</code>, &ldquo;Re-engagement message&rdquo;</td>
						<td>More than 24 hours have passed since the customer&apos;s last message. Wait for them to write again.</td>
					</tr>
					<tr>
						<td>Error <code>133016</code> when registering a number</td>
						<td>You tried to register it too many times. Meta blocks the number for 72 hours. Wait, then try once with the correct PIN.</td>
					</tr>
					<tr>
						<td>Works for a day, then fails</td>
						<td>You used the temporary token. Create a system user token (WhatsApp, step 2) and update the channel.</td>
					</tr>
				</tbody>
			</table>

			<h3>Still stuck?</h3>
			<p>
				Email <a href={mail}>{site.supportEmail}</a> and tell us the channel, roughly when it happened, what you see (a screenshot of the error helps), and the steps you tried.
				<strong> Never send your App secret or access tokens.</strong> If you shared one by mistake, reset it in Meta.
			</p>
		</>
	);
}
