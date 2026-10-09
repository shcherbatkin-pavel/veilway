import { ReactNode, useState } from "react";

function DownloadLink({ href, children }: { href: string; children: ReactNode }) {
  return <a href={href} target="_blank" rel="noopener noreferrer">{children} ↗</a>;
}

const guides = [
  {
    id: "android", label: "Android",
    install: <p>Установите OpenVPN Connect из <DownloadLink href="https://play.google.com/store/apps/details?id=net.openvpn.openvpn">Google Play</DownloadLink>.</p>,
    importTitle: "Импортируйте профиль",
    import: <p>Откройте OpenVPN Connect. Нажмите «+», выберите импорт из файла (File) и найдите скачанный файл <code>.ovpn</code>. Добавьте профиль кнопкой Add.</p>,
    connect: <p>Включите переключатель рядом с профилем и подтвердите системный запрос на VPN-подключение. Дождитесь статуса Connected — можно пользоваться интернетом. Для отключения выключите переключатель.</p>,
  },
  {
    id: "iphone", label: "iPhone",
    install: <p>Установите OpenVPN Connect из <DownloadLink href="https://apps.apple.com/app/openvpn-connect/id590379981">App Store</DownloadLink>.</p>,
    importTitle: "Импортируйте профиль",
    import: <p>Найдите скачанный файл <code>.ovpn</code> в приложении «Файлы». Откройте «Поделиться», выберите OpenVPN Connect и добавьте профиль кнопкой Add.</p>,
    connect: <p>Включите переключатель рядом с профилем и разрешите добавление VPN-конфигурации, если iPhone спросит. Дождитесь статуса Connected — можно пользоваться интернетом. Для отключения выключите переключатель.</p>,
  },
  {
    id: "windows", label: "Windows",
    install: <p>Скачайте OpenVPN Connect с <DownloadLink href="https://openvpn.net/client/">официальной страницы</DownloadLink> и установите приложение.</p>,
    importTitle: "Импортируйте профиль",
    import: <p>Откройте OpenVPN Connect. Нажмите «+», выберите File, затем Browse и укажите скачанный файл <code>.ovpn</code>. Добавьте профиль кнопкой Add.</p>,
    connect: <p>Включите переключатель рядом с профилем. Дождитесь статуса Connected — можно пользоваться интернетом. Для отключения выключите переключатель.</p>,
  },
  {
    id: "linux", label: "Linux",
    install: <><p>Для Ubuntu/Debian откройте терминал и установите OpenVPN:</p><pre><code>{"sudo apt update\nsudo apt install openvpn"}</code></pre><p>Другие варианты установки — в <DownloadLink href="https://community.openvpn.net/Pages/OpenVPN%20software%20repos">официальных репозиториях OpenVPN</DownloadLink>.</p></>,
    importTitle: "Откройте профиль в терминале",
    import: <><p>Запустите OpenVPN со скачанным файлом:</p><pre><code>{'sudo openvpn --config "/путь/к/скачанному/файлу.ovpn"'}</code></pre><p>Замените путь в кавычках на полный путь к вашему файлу, сохранив его настоящее имя. Пароль, который запросит sudo, — пароль вашей учётной записи Linux.</p></>,
    connect: <p>Дождитесь строки <code>Initialization Sequence Completed</code> — VPN подключён. Оставьте терминал открытым и пользуйтесь интернетом. Для отключения нажмите <kbd>Ctrl+C</kbd>.</p>,
  },
] as const;

export function ConnectionGuide({ onProfiles }: { onProfiles: () => void }) {
  const [platform, setPlatform] = useState<typeof guides[number]["id"]>("android");
  const guide = guides.find(item => item.id === platform)!;
  return <>
    <header className="page-heading"><div><p className="eyebrow">ИНСТРУКЦИЯ</p><h1>Как подключиться</h1><p className="muted">Выберите устройство и выполните четыре шага.</p></div><button className="secondary" onClick={onProfiles}>К профилям →</button></header>
    <div className="guide-platforms" role="group" aria-label="Выберите платформу">
      {guides.map(item => <button key={item.id} className="secondary" aria-pressed={platform === item.id} onClick={() => setPlatform(item.id)}>{item.label}</button>)}
    </div>
    <section className="panel connection-guide" aria-label={`Подключение: ${guide.label}`}>
      <h2>{guide.label === "Linux" ? "Linux — Ubuntu/Debian" : guide.label}</h2>
      <ol className="guide-steps">
        <li><h3>Установите приложение</h3>{guide.install}</li>
        <li><h3>Скачайте VPN-профиль</h3><p>Откройте раздел профилей в панели, выберите действующий профиль для устройства и нажмите «Скачать». Сохраните файл <code>.ovpn</code>.</p><p className="muted">Если профиль ещё не назначен или срок действия закончился, обратитесь к администратору.</p></li>
        <li><h3>{guide.importTitle}</h3>{guide.import}</li>
        <li><h3>Подключитесь и пользуйтесь VPN</h3>{guide.connect}</li>
      </ol>
      <p className="guide-privacy muted">Ваш профиль — личный доступ к VPN. Не передавайте файл другим людям.</p>
    </section>
  </>;
}
