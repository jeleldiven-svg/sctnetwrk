/**
 * Telegram Mini App — Premium Dashboard
 * HATA KORUMALI SÜRÜM — Siyah ekran önlenmiş
 */

const tg = window.Telegram.WebApp;

// Telegram entegrasyonu
tg.ready();
tg.expand();
tg.enableClosingConfirmation();

// API Base URL
const API_BASE = window.location.origin;

/* ============================================================
   API WRAPPER
   ============================================================ */

async function apiCall(endpoint, options = {}) {
    const url = `${API_BASE}${endpoint}`;
    const defaults = { headers: { 'Content-Type': 'application/json' } };
    const opts = { ...defaults, ...options };
    try {
        const res = await fetch(url, opts);
        const data = await res.json();
        return data;
    } catch (e) {
        console.error('API çağrısı başarısız:', endpoint, e);
        return { success: false, message: 'Sunucuya bağlanılamadı.' };
    }
}

/* ============================================================
   ANA FONKSİYON — Tüm hata durumları yakalanır
   ============================================================ */

async function loadUserData() {
    // DOM elementlerini güvenli şekilde al
    const nameEl = document.getElementById('user-name');
    const statusEl = document.getElementById('user-status');

    const user = tg.initDataUnsafe && tg.initDataUnsafe.user;

    if (!user) {
        if (nameEl) nameEl.innerText = 'Test Kullanıcısı';
        showPendingScreen();
        return;
    }

    // İsim
    if (nameEl) {
        nameEl.innerText = user.first_name + (user.last_name ? ' ' + user.last_name : '');
    }

    // Profil fotoğrafı
    if (user.photo_url) {
        const img = document.getElementById('user-photo');
        if (img) {
            const testImg = new Image();
            testImg.onload = () => { img.src = user.photo_url; };
            testImg.onerror = () => { console.log('Fotoğraf yüklenemedi, varsayılan kullanılıyor.'); };
            testImg.src = user.photo_url;
        }
    }

    // API çağrısı
    const data = await apiCall(`/api/user/${user.id}`);

    if (!data || !data.success || !data.user) {
        console.warn('API yanıtı başarısız, bekleyen ekranı göster.');
        showPendingScreen();
        return;
    }

    const userData = data.user;
    const status = (userData.status || 'PENDING').toUpperCase();

    // Durum rozeti güncelle
    if (statusEl) {
        statusEl.innerText = status;
        statusEl.className = 'badge';
        if (status === 'APPROVED') statusEl.classList.add('approved');
        else if (status === 'REJECTED') statusEl.classList.add('rejected');
        else statusEl.classList.add('pending');
    }

    // Erişim kontrolü
    if (status === 'APPROVED') {
        showDashboard();
        updateUI(userData);
    } else if (status === 'REJECTED') {
        showRejectedScreen();
    } else {
        showPendingScreen();
    }
}

/* ============================================================
   UI GÜNCELLEME — Tüm alanlar try-catch içinde
   ============================================================ */

function updateUI(user) {
    try {
        // Bakiye — güvenli hesaplamalar
        const bakiye = parseFloat(user.bakiye) || 0;
        const bloke = parseFloat(user.bloke_bakiye) || 0;
        const total = (bakiye + bloke).toFixed(2);

        const totalEl = document.getElementById('total-balance');
        const netEl = document.getElementById('net-balance');
        const blockedEl = document.getElementById('blocked-balance');

        if (totalEl) totalEl.innerHTML = `${total} <span class="currency">PUAN</span>`;
        if (netEl) netEl.innerText = bakiye.toFixed(2);
        if (blockedEl) blockedEl.innerText = bloke.toFixed(2);

        // Referans ağacı
        const refTree = user.ref_tree || { 1: 0, 2: 0, 3: 0, 4: 0 };
        const els = ['ref-l1', 'ref-l2', 'ref-l3', 'ref-l4'];
        els.forEach((id, i) => {
            const el = document.getElementById(id);
            if (el) el.innerText = refTree[i + 1] || 0;
        });

        // Referans linki
        const refLinkEl = document.getElementById('ref-link');
        if (refLinkEl && user.bot_username && user.id) {
            refLinkEl.value = `https://t.me/${user.bot_username}?start=ref_${user.id}`;
        }

        // Geçmiş
        renderHistory(user.history);
    } catch (e) {
        console.error('UI güncelleme hatası:', e);
        // Sayfa siyaha dönmesin diye catch aldık
    }
}

/* ============================================================
   EKRAN KONTROLÜ
   ============================================================ */

function showDashboard() {
    try {
        const app = document.getElementById('app');
        const pending = document.getElementById('access-denied-pending');
        const rejected = document.getElementById('access-denied-rejected');

        if (app) app.style.display = 'block';
        if (pending) pending.style.display = 'none';
        if (rejected) rejected.style.display = 'none';
    } catch (e) {
        console.error('Ekran gösterme hatası:', e);
    }
}

function showPendingScreen() {
    try {
        const app = document.getElementById('app');
        const pending = document.getElementById('access-denied-pending');
        const rejected = document.getElementById('access-denied-rejected');

        if (app) app.style.display = 'none';
        if (rejected) rejected.style.display = 'none';
        if (pending) pending.style.display = 'flex';
    } catch (e) {
        console.error('Pending ekran hatası:', e);
    }
}

function showRejectedScreen() {
    try {
        const app = document.getElementById('app');
        const pending = document.getElementById('access-denied-pending');
        const rejected = document.getElementById('access-denied-rejected');

        if (app) app.style.display = 'none';
        if (pending) pending.style.display = 'none';
        if (rejected) rejected.style.display = 'flex';
    } catch (e) {
        console.error('Rejected ekran hatası:', e);
    }
}

/* ============================================================
   GEÇMİŞ RENDER
   ============================================================ */

function renderHistory(history) {
    try {
        const container = document.getElementById('history-list');
        if (!container) return;

        if (!history || !Array.isArray(history) || history.length === 0) {
            container.innerHTML = '<p class="empty-msg">İşlem geçmişi bulunamadı.</p>';
            return;
        }

        container.innerHTML = history.map(item => {
            const durum = (item.durum || 'BILINMIYOR').toLowerCase();
            return `
                <div class="history-item">
                    <div class="info">
                        <h4>Ödül Çekimi</h4>
                        <span>${item.talep_tarihi || '—'}</span>
                    </div>
                    <div class="amount">-${item.miktar || '0'}</div>
                    <span class="status ${durum}">${item.durum || '—'}</span>
                </div>
            `;
        }).join('');
    } catch (e) {
        console.error('Geçmiş render hatası:', e);
    }
}

/* ============================================================
   MODAL
   ============================================================ */

function openSection(type) {
    try {
        const modal = document.getElementById('modal-container');
        const title = document.getElementById('modal-title');
        const body = document.getElementById('modal-body');

        if (!modal || !title || !body) return;

        modal.classList.remove('hidden');

        switch (type) {
            case 'withdraw':
                title.innerText = 'Ödül Çekme Talebi';
                body.innerHTML = `
                    <div class="modal-form">
                        <p class="modal-hint">Minimum çekim: 50 Puan</p>
                        <input type="number" id="wd-amount" class="modal-input" placeholder="Miktar giriniz" min="50">
                        <button class="submit-btn" onclick="submitWithdraw()">
                            <i class="fas fa-paper-plane"></i> Talep Oluştur
                        </button>
                    </div>
                `;
                break;

            case 'wallet':
                title.innerText = 'Cüzdan Adresim';
                body.innerHTML = `
                    <div class="modal-form">
                        <p class="modal-hint">USDT TRC20 adresinizi giriniz</p>
                        <input type="text" id="wallet-addr" class="modal-input" placeholder="USDT TRC20 Adresiniz">
                        <button class="submit-btn" onclick="updateWallet()">
                            <i class="fas fa-check"></i> Güncelle
                        </button>
                    </div>
                `;
                break;

            case 'referral':
                title.innerText = 'Referans Bilgileri';
                const l1 = parseInt(document.getElementById('ref-l1').innerText) || 0;
                const l2 = parseInt(document.getElementById('ref-l2').innerText) || 0;
                const l3 = parseInt(document.getElementById('ref-l3').innerText) || 0;
                const l4 = parseInt(document.getElementById('ref-l4').innerText) || 0;
                body.innerHTML = `
                    <div class="modal-form">
                        <div class="referral-stats">
                            <div class="ref-stat">
                                <span class="ref-label">Toplam Referans</span>
                                <span class="ref-value">${l1 + l2 + l3 + l4}</span>
                            </div>
                            <div class="ref-stat">
                                <span class="ref-label">Aktif Referans</span>
                                <span class="ref-value">${l1}</span>
                            </div>
                        </div>
                        <p class="modal-hint" style="margin-top:16px;">
                            Referans linkinizi paylaşarak PUAN kazanın.
                        </p>
                    </div>
                `;
                break;

            case 'history':
                title.innerText = 'Tüm İşlemler';
                body.innerHTML = '<div class="modal-form"><p class="empty-msg">Detaylı geçmiş API bağlantısı ile görüntülenir.</p></div>';
                break;

            default:
                title.innerText = 'Bilgi';
                body.innerHTML = '<p class="modal-hint">Bu bölüm henüz kullanıma hazır değil.</p>';
        }
    } catch (e) {
        console.error('Modal açma hatası:', e);
    }
}

function closeModal() {
    try {
        document.getElementById('modal-container').classList.add('hidden');
    } catch (e) {}
}

/* ============================================================
   İŞLEM FONKSİYONLARI
   ============================================================ */

async function submitWithdraw() {
    try {
        const amountInput = document.getElementById('wd-amount');
        const amount = parseFloat(amountInput.value);

        if (!amount || amount < 50) {
            tg.showAlert('Minimum çekim tutarı 50 PUAN\'dır.');
            return;
        }

        const user = tg.initDataUnsafe && tg.initDataUnsafe.user;
        if (!user) {
            tg.showAlert('Kullanıcı bilgisi bulunamadı.');
            return;
        }

        const btn = document.querySelector('.submit-btn');
        if (btn) {
            btn.disabled = true;
            btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Gönderiliyor...';
        }

        const res = await apiCall('/api/withdraw', {
            method: 'POST',
            body: JSON.stringify({ user_id: user.id, amount: amount.toString() })
        });

        tg.showAlert(res.message || 'İşlem tamamlandı.');

        if (res.success) {
            closeModal();
            loadUserData();
        }
    } catch (error) {
        console.error('Çekim hatası:', error);
        tg.showAlert('Bir hata oluştu, lütfen tekrar deneyin.');
    }
}

async function updateWallet() {
    try {
        const walletInput = document.getElementById('wallet-addr');
        const wallet = walletInput.value.trim();

        if (!wallet || wallet.length < 10) {
            tg.showAlert('Geçerli bir cüzdan adresi giriniz.');
            return;
        }

        const user = tg.initDataUnsafe && tg.initDataUnsafe.user;
        if (!user) {
            tg.showAlert('Kullanıcı bilgisi bulunamadı.');
            return;
        }

        const btn = document.querySelector('.submit-btn');
        if (btn) {
            btn.disabled = true;
            btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Güncelleniyor...';
        }

        const res = await apiCall('/api/wallet', {
            method: 'POST',
            body: JSON.stringify({ user_id: user.id, wallet })
        });

        tg.showAlert(res.message || 'Cüzdan güncellendi.');

        if (res.success) {
            closeModal();
            loadUserData();
        }
    } catch (error) {
        console.error('Cüzdan güncelleme hatası:', error);
        tg.showAlert('Bir hata oluştu, lütfen tekrar deneyin.');
    }
}

/* ============================================================
   REFERANS LİNK KOPYALAMA
   ============================================================ */

function copyRef() {
    try {
        const copyText = document.getElementById('ref-link');
        if (navigator.clipboard && navigator.clipboard.writeText) {
            navigator.clipboard.writeText(copyText.value).then(() => {
                tg.showAlert('Referans linki kopyalandı!');
            }).catch(() => {
                fallbackCopy(copyText);
            });
        } else {
            fallbackCopy(copyText);
        }
    } catch (e) {
        tg.showAlert('Kopyalama başarısız oldu.');
    }
}

function fallbackCopy(el) {
    try {
        el.select();
        el.setSelectionRange(0, 99999);
        document.execCommand('copy');
        tg.showAlert('Referans linki kopyalandı!');
    } catch (e) {
        tg.showAlert('Kopyalama başarısız oldu.');
    }
}

/* ============================================================
   TELEGRAM TEMA
   ============================================================ */

function applyTelegramTheme() {
    try {
        const colorScheme = tg.colorScheme;
        if (colorScheme === 'light') {
            document.documentElement.classList.add('light-theme');
        } else {
            document.documentElement.classList.remove('light-theme');
        }
    } catch (e) {}
}

try {
    tg.onEvent('themeChanged', applyTelegramTheme);
} catch (e) {}

/* ============================================================
   GLOBAL HATA YAKALAMA — Sayfa hiç siyaha dönmesin
   ============================================================ */

window.onerror = function(msg, url, line, col, error) {
    console.error('Global hata:', msg, 'Satır:', line);
    return true; // Hata swallow edilir, sayfa çökmez
};

window.addEventListener('unhandledrejection', function(event) {
    console.error('Yakalanmamış Promise hatası:', event.reason);
    event.preventDefault(); // Sayfa çökmesini önler
});

/* ============================================================
   BAŞLAT
   ============================================================ */

applyTelegramTheme();
loadUserData();
