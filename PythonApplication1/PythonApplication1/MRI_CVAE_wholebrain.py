

import tensorflow as tf
gpus = tf.config.experimental.list_physical_devices('GPU')
if gpus:
    try:
        # Currently, memory growth needs to be the same across GPUs
        for gpu in gpus:
            tf.config.experimental.set_memory_growth(gpu, True)
        logical_gpus = tf.config.experimental.list_logical_devices('GPU')
        #print(len(gpus), "Physical GPUs,", len(logical_gpus), "Logical GPUs")
    except RuntimeError as e:
        # Memory growth must be set before GPUs have been initialized
        print(e)

import numpy as np
from tensorflow import keras
from tensorflow.keras import layers
from keras.utils import to_categorical
from keras.layers.merge import concatenate
import matplotlib.pyplot as plt
import pandas as pd
import math
import glob
import matplotlib.pyplot as plt
import nibabel as nib #reading MR images

filepath_df = pd.read_csv('Z:/PRONIA_data/Tables/pronia_full_niftis.csv')

mri_types =['wp0', # whole brain
            'wp1', # gm
            'wp2', # wm
            'mwp2', # modulated? wm
            'rp1', # gm mask
            'rp2'] # wm mask

niis = []
labels = []
num_subjs =690 # max 698

for i in range(num_subjs):
    row = filepath_df.iloc[i]
    nii_path = row['wp1']
    nii = nib.load(nii_path)
    nii = nii.get_fdata()
    nii = nii[:, 35:51, :] # gives 16 slices 36-51, ones with most variance (<80% similarity) (detailed in slice_variance.csv)
    labels.append(row['STUDYGROUP'])
    for j in range(nii.shape[1]):
        niis.append((nii[:,j,:]))
        
depth = len(nii[2])
# Prepare to crop images
#def crop_center(img,cropx,cropy):
#    y,x = img.shape
#    startx = x//2-(cropx//220) # // means floor division (rounds down to whole number)
#    starty = y//2-(cropy//2)    
#    return img[starty:starty+cropy,startx:startx+cropx]
# crop images in niis list
images = []
for i in range(len(niis)):
    img = niis[i]
    img = img[20:100, 10:90] # 12:108, 2:98 is whole brain
    images.append(img)
    
images = np.asarray(images) # shape num_subjs*121*121
# reshape to matrix in able to feed into network
images = images.reshape(-1, depth, 80, 80) # num_subjs,depth,121,121, (96,96)

### min-max normalisation to rescale between 1 and 0 to improve accuracy
m = np.max(images)
mi = np.min(images) 
images = (images - mi) / (m - mi)

#from CVAE_3Dplots import plot_slices
#plot_slices(images)

## Pad images with zeros at boundaries so the dimenson is even and easier to downsample images by two while passing through model. Add in three rows and columns to make dim 176*176
#temp = np.zeros([num_subjs, depth,124,124])
#temp[:,:,3:,3:,] = images
#images = temp # dim now 5*2*124*124 # could replace temp with images 

# test train split (no labels for vae)
from sklearn.model_selection import train_test_split
x_train,x_test,y_train,y_test = train_test_split(images, labels, test_size=0.2, random_state=13, stratify = labels)
train_label, test_label = y_train, y_test
y_train = to_categorical(y_train) # tuple num_patients * num_labels convert to onehot
y_test = to_categorical(y_test) # tuple num_patients * num_labels

 # Autoencoder variables
epochs = 200
batch_size = 8
#intermediate_dim = 124
latent_dim = 250
n_y = y_train.shape[1] # 2
n_x = x_train.shape[1] # 784
n_z = 2 # depth?
X, y = len(images[0][0][0]), len(images[0][0][0]) # should be 96, 96 messy fix though
inchannel = 1
origin_dim = 28*28 # why is this set


# Make a sampling layer, this maps the MNIST digit to latent-space triplet (z_mean, z_log_var, z), this is how the bottleneck is displayed. 
from keras import backend as K
def sampling(args):
    """Uses (z_mean, z_log_var) to sample z, the vector encoding a digit."""
    z_mean, z_log_sigma = args
    epsilon = K.random_normal(shape=(K.shape(z_mean)[0], latent_dim), mean = 0., stddev=0.1)
    return z_mean + K.exp(z_log_sigma) * epsilon

# Build the model
from tensorflow.keras.layers import Input, BatchNormalization, Conv3D, Dense, Flatten, Lambda, Reshape, Conv3DTranspose, BatchNormalization, SpatialDropout3D
# Encoder
label = keras.Input(shape=(n_y, )) # shape of length y_train
encoder_inputs = keras.Input(shape=(depth, X, y, inchannel)) # it will add a None layer as batch size
x = layers.Conv3D(32, (3, 3, 3), activation="relu", padding="same")(encoder_inputs) # relu turns negative values to 0
x = layers.MaxPooling3D(pool_size=(2, 2, 2))(x) # max pooling 
#x = layers.SpatialDropout3D(0.3)(x)
x = layers.Conv3D(64, (3, 3, 3), activation="relu",  padding="same")(x)
x = layers.MaxPooling3D(pool_size=(2, 2, 2), padding ='same')(x) 
#x = layers.SpatialDropout3D(0.2)(x)
x = layers.Conv3D(128, (3, 3, 3), activation="relu",  padding="same")(x)
x = layers.MaxPooling3D(pool_size=(2, 2, 2), padding='same')(x) 
#x = layers.SpatialDropout3D(0.3)(x)
#x = layers.Conv3D(256, (3, 3, 3), activation="relu",  padding="same")(x)
x = layers.Flatten()(x) # to feed into sampling function
z_mean = layers.Dense(latent_dim, name="z_mean")(x)
z_log_sigma = layers.Dense(latent_dim, name="z_log_var")(x)
z = layers.Lambda(sampling, name='z')([z_mean, z_log_sigma])
z_label = concatenate([z, label], name='encoded') 
## initiating the encoder, it ouputs the latent dim dimensions
encoder = keras.Model([encoder_inputs, label], [z_mean, z_log_sigma, z_label, z], name="encoder")
encoder.summary()

#### Make the decoder, takes the latent keras
latent_inputs = keras.Input(shape=(latent_dim + n_y),) # changes based on depth 
x =  layers.Dense(2*10*10*128, activation='relu')(latent_inputs)
x = layers.Reshape((2, 10, 10, 128))(x)
#x = layers.Conv3DTranspose(128, (3, 3, 3), activation="relu", padding="same")(x)
x = layers.UpSampling3D((2,2,2))(x)
#x = layers.SpatialDropout3D(0.3)(x)
x = layers.Conv3DTranspose(64, (3, 3, 3), activation="relu",  padding="same")(x)
x = layers.UpSampling3D((2,2,2))(x)
#x = layers.SpatialDropout3D(0.2)(x)
x = layers.Conv3DTranspose(32, (3, 3, 3), activation="relu",  padding="same")(x)
x = layers.UpSampling3D((2,2,2))(x)
#x = layers.SpatialDropout3D(0.3)(x)
decoder_outputs = layers.Conv3DTranspose(1, 3, activation="sigmoid", padding="same")(x)
# Initiate decoder
decoder = keras.Model(latent_inputs, decoder_outputs, name="decoder")
decoder.summary()

# Instantiate and fit VAE model, outputs only z_label
outputs = decoder(encoder([encoder_inputs, label])[2])
cvae = keras.Model([encoder_inputs, label], outputs, name='cvae')
cvae.summary()

# use a custom loss function, this includes a KL divergence regularisation term which ensures that z is close to normal (0 mean, 1 sd)
reconstruction_loss = keras.losses.binary_crossentropy(encoder_inputs, outputs)
reconstruction_loss *= origin_dim
reconstruction_loss = K.mean(reconstruction_loss) # mean to avoid incompatible shape error
kl_loss = 1 + z_log_sigma - K.square(z_mean) - K.exp(z_log_sigma)
kl_loss = K.sum(kl_loss, axis=-1)
kl_loss *= -0.5
cvae_loss = reconstruction_loss + kl_loss # mean was worse

# Add loss and compile cvae model
cvae.add_loss(cvae_loss)
opt = keras.optimizers.Adam(learning_rate = 0.0001, beta_1 = 0.009)
cvae.compile(optimizer=opt)

# Tensorboard
from keras.callbacks import TensorBoard
import datetime
log_dir = "C:/Users/Mischa/sophie/29_06_21_onwards/wholebrain" + datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
tensorboard_callback = TensorBoard(log_dir=log_dir)

# Adding early stopping
es_callback = keras.callbacks.EarlyStopping(monitor='val_loss', patience=5)

# fit the data 
history = cvae.fit([x_train, y_train], x_train, epochs=epochs, batch_size=batch_size, validation_data = ([x_test, y_test], x_test), verbose = 2, callbacks=[tensorboard_callback, es_callback])

