document.documentElement.classList.add("js");

const siteHeader = document.querySelector(".site-header");
const menuButton = document.getElementById("menuButton");
const mobileNav = document.getElementById("mobileNav");

if (menuButton && mobileNav) {
    menuButton.addEventListener("click", () => {
        const isOpen = mobileNav.classList.toggle("open");
        menuButton.setAttribute("aria-expanded", String(isOpen));
        menuButton.setAttribute("aria-label", isOpen ? "Close menu" : "Open menu");
    });
}

if (mobileNav) {
    mobileNav.querySelectorAll("a").forEach((link) => {
        link.addEventListener("click", () => {
            mobileNav.classList.remove("open");
            if (menuButton) {
                menuButton.setAttribute("aria-expanded", "false");
                menuButton.setAttribute("aria-label", "Open menu");
            }
        });
    });
}

if (siteHeader) {
    const updateHeader = () => {
        siteHeader.classList.toggle("scrolled", window.scrollY > 12);
    };

    updateHeader();
    window.addEventListener("scroll", updateHeader, { passive: true });
}

const revealItems = document.querySelectorAll("[data-reveal]");

if (revealItems.length) {
    if ("IntersectionObserver" in window) {
        const revealObserver = new IntersectionObserver((entries, observer) => {
            entries.forEach((entry) => {
                if (entry.isIntersecting) {
                    entry.target.classList.add("is-visible");
                    observer.unobserve(entry.target);
                }
            });
        }, { threshold: 0.12, rootMargin: "0px 0px -40px" });

        revealItems.forEach((item) => revealObserver.observe(item));
    } else {
        revealItems.forEach((item) => item.classList.add("is-visible"));
    }
}

const sizePicker = document.getElementById("sizePicker");
const whatsappButtons = document.querySelectorAll(".js-whatsapp-order");

if (sizePicker && whatsappButtons.length) {
    const product = sizePicker.dataset.product;
    const price = sizePicker.dataset.price;
    const choices = sizePicker.querySelectorAll(".size-choice");

    const buildLink = (whatsapp, size) => {
        const message =
            `Hello Divine Fit, I am interested in the ${product} ` +
            `(${price}). My preferred size is ${size}. Is it available?`;

        return `https://wa.me/${whatsapp}?text=${encodeURIComponent(message)}`;
    };

    const updateOrderLinks = (size) => {
        whatsappButtons.forEach((button) => {
            button.href = buildLink(button.dataset.wa, size);
        });
    };

    choices.forEach((choice) => {
        choice.addEventListener("click", () => {
            choices.forEach((item) => item.classList.remove("selected"));
            choice.classList.add("selected");
            updateOrderLinks(choice.dataset.size);
        });
    });

    const selected = sizePicker.querySelector(".size-choice.selected");

    if (selected) {
        updateOrderLinks(selected.dataset.size);
    }
}

const addImageInput = document.getElementById("image");
const addImagePreview = document.getElementById("imagePreview");

if (addImageInput && addImagePreview) {
    addImageInput.addEventListener("change", () => {
        const file = addImageInput.files && addImageInput.files[0];

        addImagePreview.innerHTML = "";
        addImagePreview.classList.remove("has-image");

        if (!file) {
            return;
        }

        const image = document.createElement("img");
        image.src = URL.createObjectURL(file);
        image.alt = "Selected dress preview";
        image.onload = () => URL.revokeObjectURL(image.src);

        addImagePreview.appendChild(image);
        addImagePreview.classList.add("has-image");
    });
}
